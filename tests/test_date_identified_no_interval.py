"""`dateIdentified` may not be a date range (migration 0069).

An identification is made *on a date*, not over a span, and TaxonWorks refuses the range
outright — its DwC importer raises "Date range for taxon determination is not supported."
(TW @ 897f385, `dataset_record/darwin_core/occurrence.rb:1395-1397`). This used to be
caught only by `dwc_export._validate_row`, i.e. at export time, long after the value was
typed; now the bad state is unrepresentable.

Three layers, deliberately: the UI validator refuses it on blur, `specimens._reject_interval`
refuses it in every save path (forms, Import & Assign, bulk import) with a sentence the
user can act on, and a DB CHECK backstops both — CLAUDE.md §2, "prefer DB-enforced
constraints over application-level hope".

**The asymmetry is the point:** `collecting_event."dwc:eventDate"` still accepts an
interval, because a collecting trip really does span days and TaxonWorks accepts one
there. Only the determination is constrained.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Taxon, TaxonDetermination
from app.models.base import _utcnow
import app.services.field_occurrence as fo_svc
from app.services.dwc_import import normalise_row_dates
from app.services.events import create_collecting_event
from app.services.specimens import (
    create_collection_object, create_determination, update_determination_metadata,
)
from tests.helpers import ensure_repo

_RANGE = "2026-01-04/2026-02-20"


@pytest.fixture
def subject(session):
    """A specimen and a taxon to hang determinations on."""
    tx = Taxon(name_element="crypticus", scientific_name="Otiorhynchus crypticus",
               taxon_rank="species", nomenclatural_code="ICZN",
               created_at=_utcnow(), updated_at=_utcnow())
    session.add(tx)
    session.flush()
    co = create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-20001",
        repository_id=ensure_repo(session, "JJPC"),
    )
    session.flush()
    return co, tx


def test_creating_a_determination_with_a_range_is_refused(session, subject):
    co, tx = subject
    with pytest.raises(ValueError, match="cannot be a date range"):
        create_determination(session, collection_object_id=co.id, taxon_id=tx.id,
                             date_identified=_RANGE)


def test_the_message_says_what_to_do_about_it(session, subject):
    """A refusal the user cannot act on is only half a guard."""
    co, tx = subject
    with pytest.raises(ValueError) as exc:
        create_determination(session, collection_object_id=co.id, taxon_id=tx.id,
                             date_identified=_RANGE)
    message = str(exc.value)
    assert _RANGE in message                       # which value was refused
    assert "TaxonWorks" in message                 # why it cannot be kept
    assert "single date" in message                # what to do instead


def test_updating_a_determination_to_a_range_is_refused(session, subject):
    co, tx = subject
    det = create_determination(session, collection_object_id=co.id, taxon_id=tx.id,
                               date_identified="2026-01-04")
    with pytest.raises(ValueError, match="cannot be a date range"):
        update_determination_metadata(
            session, det.id, sex=None, type_status=None, identified_by_id=None,
            date_identified=_RANGE, identification_qualifier=None,
            identification_remarks=None,
        )


@pytest.mark.parametrize("value", ["2026", "2026-01", "2026-01-04", None, ""])
def test_a_single_or_partial_date_is_fine(session, subject, value):
    """Only the *range* is refused — an imprecise single date is legitimate and common
    (a determination known only to the year)."""
    co, tx = subject
    det = create_determination(session, collection_object_id=co.id, taxon_id=tx.id,
                               date_identified=value)
    assert det.date_identified == (value or None)


def test_the_database_refuses_a_range_written_around_the_service(session, subject):
    """The backstop: a raw attribute write (or any future code path that forgets the
    guard) still cannot store one. This is the layer that makes the state
    unrepresentable rather than merely discouraged."""
    co, tx = subject
    det = create_determination(session, collection_object_id=co.id, taxon_id=tx.id,
                               date_identified="2026-01-04")
    det.date_identified = _RANGE
    with pytest.raises(IntegrityError, match="ck_td_date_identified_no_interval"):
        session.flush()
    session.rollback()


def test_an_event_date_may_still_be_a_range(session):
    """The asymmetry — a collecting trip spans days, and TaxonWorks accepts an interval
    on eventDate. Constraining it here would be wrong, not merely stricter."""
    ev = create_collecting_event(session, locality="Somewhere", event_date=_RANGE)
    session.flush()
    assert ev.event_date == _RANGE


def test_a_field_occurrence_determination_is_constrained_too(session):
    """Same column, same rule — the CHECK is on the table, so it holds for the other
    side of the determination's subject arc too.

    The field occurrence is created properly rather than left null, so the subject arc
    is *satisfied* and the date CHECK is the only constraint that can fire — otherwise
    this would pass on the arc alone and prove nothing.
    """
    tx = Taxon(name_element="robur", scientific_name="Quercus robur",
               taxon_rank="species", nomenclatural_code="ICN",
               created_at=_utcnow(), updated_at=_utcnow())
    session.add(tx)
    session.flush()
    ev = create_collecting_event(session, locality="A hedgerow")
    session.flush()
    fo = fo_svc.create_field_occurrence(
        session, collecting_event_id=ev.id, taxon_id=tx.id)
    session.flush()
    det = TaxonDetermination(
        collection_object_id=None, field_occurrence_id=fo.id, taxon_id=tx.id,
        date_identified=_RANGE, is_current=0,   # 0: the fo already has a current one
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(det)
    with pytest.raises(IntegrityError, match="ck_td_date_identified_no_interval"):
        session.flush()
    session.rollback()


def test_an_imported_row_with_a_range_is_refused_at_parse_time():
    """Bulk import stages before it writes, so the range must be caught by the shared
    date normaliser — not left to blow up mid-write. A row staged `ready` that then
    raises is the two-phase design failing at its one job."""
    from app.services.dwc_import import normalise_row_dates
    overrides, err = normalise_row_dates(
        {"eventDate": "2024-06-15", "dateIdentified": _RANGE})
    assert overrides == {}
    assert err is not None and "dateIdentified" in err


def test_an_imported_row_may_still_carry_an_event_date_range():
    """Same call, the other column — the asymmetry has to survive the import path too."""
    overrides, err = normalise_row_dates(
        {"eventDate": _RANGE, "dateIdentified": "2026-01-04"})
    assert err is None
    assert overrides["event_date"] == _RANGE
