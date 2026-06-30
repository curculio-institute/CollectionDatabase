"""disposition as an editable controlled vocabulary (#76).

Was a fixed-CHECK dwc:disposition TEXT column; migration 0048 made it a single-name
vocab (disposition table + collection_object.disposition_id FK). Confirms the seed,
that an *open* user-coined value works (the point of the change), and the FK round-trip.
"""
from app.models import CollectionObject, Disposition
from app.models.base import _utcnow
from app.services.specimens import create_collection_object, update_collection_object
from app.services.vocabularies import disposition_vocab
from tests.helpers import ensure_repo


def test_seed_values_present(session):
    names = {d.name for d in session.query(Disposition).all()}
    assert {"in collection", "on loan", "donated",
            "exchanged", "missing", "destroyed"} <= names


def test_open_value_can_be_added_and_assigned(session):
    """The whole point of #76: an arbitrary holding the old CHECK would have rejected."""
    rid = ensure_repo(session, "JJPC")
    loaned = disposition_vocab.get_or_create(session, "loaned to Jeffrey")
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number="d01",
        repository_id=rid, disposition_id=loaned.id,
    )
    session.flush()
    got = session.get(CollectionObject, co.id)
    assert got.disposition.name == "loaned to Jeffrey"


def test_disposition_can_be_changed_and_cleared(session):
    rid = ensure_repo(session, "JJPC")
    inc = disposition_vocab.get_or_create(session, "in collection")
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number="d02",
        repository_id=rid, disposition_id=inc.id,
    )
    session.flush()
    on_loan = disposition_vocab.get_or_create(session, "on loan")
    update_collection_object(session, co.id, disposition_id=on_loan.id)
    assert session.get(CollectionObject, co.id).disposition.name == "on loan"
    # clearing it (no disposition) — nullable FK
    update_collection_object(session, co.id, disposition_id=None)
    assert session.get(CollectionObject, co.id).disposition_id is None
