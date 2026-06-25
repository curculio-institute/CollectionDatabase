"""Reared-specimen life-stage history (#50).

CRUD for ``life_stage_record`` rows (additional life-stage occurrence facets of a reared
specimen) plus an export-projection helper that the future DwC export (Phase 3) will use to
emit one record per life stage, linked to the preserved specimen.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LifeStageRecord, CollectionObject


def add_life_stage(
    session: Session,
    *,
    collection_object_id: int,
    life_stage: str,
    basis_of_record: str = "HumanObservation",
    event_date: Optional[str] = None,
    remarks: Optional[str] = None,
    sort_order: int = 0,
) -> LifeStageRecord:
    if not (life_stage or "").strip():
        raise ValueError("life_stage cannot be blank")
    row = LifeStageRecord(
        collection_object_id=collection_object_id,
        life_stage=life_stage.strip(),
        basis_of_record=basis_of_record or "HumanObservation",
        event_date=(event_date or None),
        remarks=(remarks or None),
        sort_order=sort_order,
    )
    session.add(row)
    session.flush()
    return row


def list_life_stages(session: Session, co_id: int) -> list[LifeStageRecord]:
    return list(session.scalars(
        select(LifeStageRecord).where(LifeStageRecord.collection_object_id == co_id)
        .order_by(LifeStageRecord.sort_order, LifeStageRecord.id)
    ).all())


def count_life_stages(session: Session, co_id: int) -> int:
    return session.query(LifeStageRecord).filter(
        LifeStageRecord.collection_object_id == co_id).count()


def delete_life_stage(session: Session, row_id: int) -> None:
    row = session.get(LifeStageRecord, row_id)
    if row is not None:
        session.delete(row)
        session.flush()


def life_stage_facets(session: Session, co_id: int) -> list[dict]:
    """Occurrence facets for a reared specimen, in export order: the preserved specimen
    first (from ``collection_object``), then each life-stage row (e.g. the wild larva).

    This is what the DwC export (Phase 3) consumes: it will mint an occurrenceID per facet
    and link the life-stage facets to the preserved one via dwc:associatedOccurrences /
    a resourceRelationship. Returned dicts carry the DwC-relevant fields only; the shared
    locality comes from the specimen's collecting_event at export time. ``role`` marks the
    preserved (primary) facet vs the derived life-stage facets.
    """
    co = session.get(CollectionObject, co_id)
    facets: list[dict] = []
    if co is not None:
        facets.append({
            "role": "preserved",
            "life_stage": co.life_stage,
            "basis_of_record": co.basis_of_record,
            "event_date": None,   # filled from the collecting_event at export time
        })
    for r in list_life_stages(session, co_id):
        facets.append({
            "role": "life_stage",
            "life_stage": r.life_stage,
            "basis_of_record": r.basis_of_record,
            "event_date": r.event_date,
        })
    return facets
