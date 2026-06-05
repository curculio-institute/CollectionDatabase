from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import (
    CollectionObject,
    CollectingEvent,
    Taxon,
    TaxonDetermination,
)
from app.models.base import _utcnow
from app.services.events import create_collecting_event
from app.services.taxa import format_scientific_name


@dataclass(frozen=True)
class RecentRow:
    collection_object_id: int
    catalog_number: str
    catalog_namespace: str
    scientific_name: str
    sex: str | None
    individual_count: int | None
    country: str | None
    locality: str | None
    event_date: str | None
    recorded_by: str | None
    identified_by: str | None


def create_collection_object(
    session: Session,
    *,
    collecting_event_id: int | None,
    catalog_number: str,
    catalog_namespace: str,
    **fields,
) -> CollectionObject:
    co = CollectionObject(
        collecting_event_id=collecting_event_id,
        catalog_number=catalog_number,
        catalog_namespace=catalog_namespace,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    for attr, val in fields.items():
        if val is None or val == "":
            continue
        if attr == "individual_count":
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        setattr(co, attr, val)
    session.add(co)
    session.flush()
    return co


def create_determination(
    session: Session,
    *,
    collection_object_id: int,
    taxon_id: int,
    identified_by: str | None = None,
    date_identified: str | None = None,
    identification_qualifier: str | None = None,
    identification_remarks: str | None = None,
    verbatim_identification: str | None = None,
    is_current: int = 1,
) -> TaxonDetermination:
    td = TaxonDetermination(
        collection_object_id=collection_object_id,
        taxon_id=taxon_id,
        identified_by=identified_by or None,
        date_identified=date_identified or None,
        identification_qualifier=identification_qualifier or None,
        identification_remarks=identification_remarks or None,
        verbatim_identification=verbatim_identification or None,
        is_current=is_current,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(td)
    session.flush()
    return td


def save_specimen_entry(
    session: Session,
    *,
    taxon_id: int,
    event_id: int | None,
    event_fields: dict,
    specimen_fields: dict,
    determination_fields: dict,
) -> CollectionObject:
    """Orchestrator: create/reuse event, then specimen, then determination in
    one transaction. Caller owns session.begin()."""
    if event_id is None:
        ce = create_collecting_event(session, **event_fields)
        eid = ce.id
    else:
        eid = event_id

    co = create_collection_object(session, collecting_event_id=eid, **specimen_fields)
    create_determination(
        session, collection_object_id=co.id, taxon_id=taxon_id, **determination_fields
    )
    return co


def recent_specimens(session: Session, limit: int = 200) -> list[RecentRow]:
    """Latest `limit` specimens with their current determination and event."""
    rows = (
        session.query(CollectionObject, TaxonDetermination, CollectingEvent, Taxon)
        .outerjoin(
            TaxonDetermination,
            and_(
                TaxonDetermination.collection_object_id == CollectionObject.id,
                TaxonDetermination.is_current == 1,
            ),
        )
        .outerjoin(CollectingEvent, CollectingEvent.id == CollectionObject.collecting_event_id)
        .outerjoin(Taxon, Taxon.id == TaxonDetermination.taxon_id)
        .order_by(CollectionObject.id.desc())
        .limit(limit)
        .all()
    )
    return [
        RecentRow(
            collection_object_id=co.id,
            catalog_number=co.catalog_number,
            catalog_namespace=co.catalog_namespace,
            scientific_name=format_scientific_name(t) if t else "",
            sex=co.sex,
            individual_count=co.individual_count,
            country=ce.country if ce else None,
            locality=(ce.locality or ce.verbatim_locality) if ce else None,
            event_date=(ce.event_date or ce.verbatim_event_date) if ce else None,
            recorded_by=ce.recorded_by if ce else None,
            identified_by=td.identified_by if td else None,
        )
        for co, td, ce, t in rows
    ]
