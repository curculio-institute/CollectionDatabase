"""External resource identifiers: source detection, CRUD, exclusive arc (#49)."""
import pytest
from sqlalchemy.orm import sessionmaker

import app.services.external_ids as ext_svc
from app.models import ExternalIdentifier, CollectingEvent, CollectionObject
from tests.helpers import ensure_repo


@pytest.fixture
def s(engine):
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as sess:
        yield sess


def test_add_list_count_delete(s):
    co = CollectionObject(catalog_number="JJPC-50001", repository_id=ensure_repo(s, "JJPC"))
    s.add(co); s.flush()
    # The user supplies just the URI; source is left unset.
    ext_svc.add_identifier(s, target_kind="collection_object", target_id=co.id,
                           value="https://inaturalist.org/observations/1")
    ext_svc.add_identifier(s, target_kind="collection_object", target_id=co.id,
                           value="MN908947")
    s.flush()
    assert ext_svc.count_identifiers(s, target_kind="collection_object", target_id=co.id) == 2
    rows = ext_svc.list_identifiers(s, target_kind="collection_object", target_id=co.id)
    assert {r.value for r in rows} == {"https://inaturalist.org/observations/1", "MN908947"}
    assert all(r.source is None for r in rows)   # source unpopulated for now
    ext_svc.delete_identifier(s, rows[0].id)
    s.flush()
    assert ext_svc.count_identifiers(s, target_kind="collection_object", target_id=co.id) == 1


def test_blank_value_rejected(s):
    co = CollectionObject(catalog_number="JJPC-50002", repository_id=ensure_repo(s, "JJPC"))
    s.add(co); s.flush()
    with pytest.raises(ValueError):
        ext_svc.add_identifier(s, target_kind="collection_object", target_id=co.id,
                               source="URL", value="   ")


def test_exclusive_arc_enforced(s):
    """A row must attach to exactly one target — neither set violates the CHECK."""
    from sqlalchemy.exc import IntegrityError
    s.add(ExternalIdentifier(source="URL", value="x"))   # no target FK set
    with pytest.raises(IntegrityError):
        s.flush()
