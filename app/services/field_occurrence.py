"""FieldOccurrence CRUD — HumanObservation records (host plants, sightings).

A field occurrence is something recorded but not physically collected (see
docs/field_occurrence.md). Its taxon determination is a ``taxon_determination`` row via
that table's subject exclusive arc, so it reuses the whole determination machinery —
including the open-nomenclature qualifier, which is the *only* identification field a host
observation exposes to the user; ``identifiedBy`` defaults to the event's ``recordedBy``
and ``basisOfRecord`` is HumanObservation, both applied automatically.

Biological associations whose object is a host/associated organism always create one of
these (decided 2026-07-11) — there is no bare ``object_taxon`` write path in the UI.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.base import _utcnow
from app.models import FieldOccurrence, TaxonDetermination, CollectingEvent
from app.services.taxa import compose_scientific_name, format_scientific_name


def create_field_occurrence(
    session: Session,
    *,
    collecting_event_id: int,
    taxon_id: int,
    identification_qualifier: str | None = None,
    identified_by_id: int | None = None,
    individual_count: int = 1,
    basis_of_record: str = "HumanObservation",
    sex: str | None = None,
    life_stage: str | None = None,
    occurrence_remarks: str | None = None,
    verbatim_identification: str | None = None,
) -> FieldOccurrence:
    """Create a FieldOccurrence + its current TaxonDetermination in one step.

    The determination freezes the composed name (Epic #30) as verbatimIdentification
    unless one is supplied; the qualifier lives separately. Caller owns the transaction.
    """
    now = _utcnow()
    fo = FieldOccurrence(
        collecting_event_id=collecting_event_id,
        basis_of_record=basis_of_record,
        individual_count=individual_count,
        sex=sex,
        life_stage=life_stage,
        occurrence_remarks=occurrence_remarks,
        created_at=now,
        updated_at=now,
    )
    session.add(fo)
    session.flush()  # assign fo.id

    if verbatim_identification is None:
        from app.models import Taxon
        tx = session.get(Taxon, taxon_id)
        verbatim_identification = compose_scientific_name(session, tx) if tx else None

    det = TaxonDetermination(
        field_occurrence_id=fo.id,
        taxon_id=taxon_id,
        identification_qualifier=identification_qualifier or None,
        identified_by_id=identified_by_id,
        verbatim_identification=verbatim_identification,
        is_current=1,
        created_at=now,
        updated_at=now,
    )
    session.add(det)
    session.flush()
    return fo


def current_determination(session: Session, fo: FieldOccurrence) -> TaxonDetermination | None:
    """The field occurrence's current determination (is_current=1), or None."""
    for det in fo.determinations:
        if det.is_current:
            return det
    return None


def object_label(session: Session, fo: FieldOccurrence) -> str:
    """A display label for a field occurrence used as an association object — the
    composed scientific name of its current determination, or a fallback."""
    det = current_determination(session, fo)
    if det and det.taxon:
        return format_scientific_name(det.taxon)
    return f"observation #{fo.id}"


def event_recorded_by_id(session: Session, collecting_event_id: int) -> int | None:
    """The event's recordedBy person id — the default identifiedBy for a field
    occurrence observed at that event."""
    ev = session.get(CollectingEvent, collecting_event_id)
    return ev.recorded_by_id if ev else None
