"""identificationQualifier: a closed CHECK-constrained set (migration 0058, #3)."""
import pytest
from sqlalchemy import text
from app.vocab import IDENTIFICATION_QUALIFIERS, IDENTIFICATION_QUALIFIER_OPTIONS
from app.services.specimens import create_collection_object, create_determination
from app.services import dwc_import as dwc_svc
from tests.helpers import ensure_repo


def _co(session):
    return create_collection_object(
        session, collecting_event_id=None,
        catalog_number="qa01", repository_id=ensure_repo(session, "Doe"))


@pytest.mark.parametrize("q", IDENTIFICATION_QUALIFIERS)
def test_every_listed_qualifier_is_accepted(session, q):
    co = _co(session)
    d = create_determination(session, collection_object_id=co.id, taxon_id=_a_taxon(session).id,
                             identification_qualifier=q)
    session.flush()
    assert d.identification_qualifier == q


def test_null_qualifier_accepted(session):
    co = _co(session)
    d = create_determination(session, collection_object_id=co.id, taxon_id=_a_taxon(session).id,
                             identification_qualifier=None)
    session.flush()
    assert d.identification_qualifier is None


def test_off_list_qualifier_rejected_by_the_db(session):
    from sqlalchemy.exc import IntegrityError
    co = _co(session)
    tax = _a_taxon(session)
    with pytest.raises(IntegrityError, match="ck_td_identification_qualifier"):
        # create_determination flushes internally, so the CHECK fires here.
        create_determination(session, collection_object_id=co.id, taxon_id=tax.id,
                             identification_qualifier="bogus")


def test_cf_is_first_blank_is_last():
    assert IDENTIFICATION_QUALIFIER_OPTIONS[0] == "cf."
    assert IDENTIFICATION_QUALIFIER_OPTIONS[-1] == ""


def test_import_reads_qualifier_and_remarks():
    det = dwc_svc.row_to_determination_fields(
        {"scientificName": "Trechus quadristriatus",
         "identificationQualifier": "cf.", "identificationRemarks": "worn"})
    assert det["identification_qualifier"] == "cf."
    assert det["identification_remarks"] == "worn"        # free text, not constrained


def _a_taxon(session):
    from app.models import Taxon
    from app.models.base import _utcnow
    t = session.query(Taxon).first()
    if t is None:
        t = Taxon(name_element="Testus", scientific_name="Testus", taxon_rank="genus",
                  nomenclatural_code="ICZN", created_at=_utcnow(), updated_at=_utcnow())
        session.add(t); session.flush()
    return t
