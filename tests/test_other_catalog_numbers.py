"""collection_object.other_catalog_numbers — prior catalog numbers, free text (#77)."""
from app.models import CollectionObject
from app.services.specimens import create_collection_object, update_collection_object
from tests.helpers import ensure_repo


def test_other_catalog_numbers_round_trip(session):
    rid = ensure_repo(session, "JJPC")
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number="oc01",
        repository_id=rid, other_catalog_numbers="NHMW 12345; coll. Smith 7",
    )
    session.flush()
    assert session.get(CollectionObject, co.id).other_catalog_numbers == "NHMW 12345; coll. Smith 7"


def test_other_catalog_numbers_editable_and_clearable(session):
    rid = ensure_repo(session, "JJPC")
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number="oc02", repository_id=rid,
    )
    session.flush()
    assert co.other_catalog_numbers is None  # optional, defaults NULL
    update_collection_object(session, co.id, other_catalog_numbers="ex coll. Jones 42")
    assert session.get(CollectionObject, co.id).other_catalog_numbers == "ex coll. Jones 42"
    # blanking clears it (update maps "" → None)
    update_collection_object(session, co.id, other_catalog_numbers="")
    assert session.get(CollectionObject, co.id).other_catalog_numbers is None
