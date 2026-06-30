"""Reared-specimen life-stage history: CRUD, basisOfRecord CHECK, export facets (#50)."""
import pytest
from sqlalchemy.orm import sessionmaker

import app.services.life_stage as ls_svc
from app.models import CollectionObject, LifeStageRecord
from tests.helpers import ensure_repo


@pytest.fixture
def s(engine):
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as sess:
        yield sess


def _co(s, cat="JJPC-60001", life_stage="adult", basis="PreservedSpecimen"):
    co = CollectionObject(catalog_number=cat, repository_id=ensure_repo(s, "JJPC"),
                          life_stage=life_stage, basis_of_record=basis)
    s.add(co); s.flush()
    return co


def test_add_list_count_delete(s):
    co = _co(s)
    ls_svc.add_life_stage(s, collection_object_id=co.id, life_stage="larva",
                          basis_of_record="HumanObservation", event_date="2024-05-01")
    ls_svc.add_life_stage(s, collection_object_id=co.id, life_stage="pupa",
                          event_date="2024-05-20")
    s.flush()
    assert ls_svc.count_life_stages(s, co.id) == 2
    rows = ls_svc.list_life_stages(s, co.id)
    assert [r.life_stage for r in rows] == ["larva", "pupa"]
    assert rows[1].basis_of_record == "HumanObservation"   # default
    ls_svc.delete_life_stage(s, rows[0].id)
    s.flush()
    assert ls_svc.count_life_stages(s, co.id) == 1


def test_blank_life_stage_rejected(s):
    co = _co(s, cat="JJPC-60002")
    with pytest.raises(ValueError):
        ls_svc.add_life_stage(s, collection_object_id=co.id, life_stage="  ")


def test_basis_of_record_check(s):
    """The DB CHECK rejects an out-of-vocabulary basisOfRecord."""
    from sqlalchemy.exc import IntegrityError
    co = _co(s, cat="JJPC-60003")
    s.add(LifeStageRecord(collection_object_id=co.id, life_stage="larva",
                          basis_of_record="Nonsense"))
    with pytest.raises(IntegrityError):
        s.flush()


def test_life_stage_facets_preserved_first(s):
    """Export facets: the preserved specimen first, then each life-stage row in order."""
    co = _co(s, cat="JJPC-60004", life_stage="adult", basis="PreservedSpecimen")
    ls_svc.add_life_stage(s, collection_object_id=co.id, life_stage="larva",
                          basis_of_record="HumanObservation", event_date="2024-05-01")
    s.flush()
    facets = ls_svc.life_stage_facets(s, co.id)
    assert facets[0] == {"role": "preserved", "life_stage": "adult",
                         "basis_of_record": "PreservedSpecimen", "event_date": None}
    assert facets[1]["role"] == "life_stage"
    assert facets[1]["life_stage"] == "larva"
    assert facets[1]["basis_of_record"] == "HumanObservation"
    assert facets[1]["event_date"] == "2024-05-01"
