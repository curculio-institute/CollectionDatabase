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
from app.services.biological import save_biological_association
from app.services.events import create_collecting_event
from app.services.identifiers import assign_code
from app.services.print_queue import (
    enqueue_data,
    enqueue_determination,
    enqueue_identifier,
)
from app.services.taxa import format_scientific_name


@dataclass(frozen=True)
class RecentRow:
    collection_object_id: int
    catalog_number: str
    collection_code: str
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
    collection_code: str,
    institution_code: str,
    **fields,
) -> CollectionObject:
    co = CollectionObject(
        collecting_event_id=collecting_event_id,
        catalog_number=catalog_number,
        collection_code=collection_code,
        institution_code=institution_code,
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
    sex: str | None = None,
    type_status: str | None = None,
    identified_by_id: int | None = None,
    date_identified: str | None = None,
    identification_qualifier: str | None = None,
    identification_remarks: str | None = None,
    verbatim_identification: str | None = None,
    is_current: int = 1,
) -> TaxonDetermination:
    td = TaxonDetermination(
        collection_object_id=collection_object_id,
        taxon_id=taxon_id,
        sex=sex or None,
        type_status=type_status or None,
        identified_by_id=identified_by_id,
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


def finalize_specimen(
    session: Session,
    *,
    collection_object_id: int,
    code: str | None,
    queue_labels: bool = False,
    print_group_id: int | None = None,
    source: str | None = None,
    associations=(),
) -> list:
    """Post-create finalization shared by every create path (Digitize standard,
    Visiting, Mounting): bind the reserved identifier code, optionally queue its
    labels, and persist biological associations. Caller owns the transaction.

    `code` is the reserved 4-char/sequential code to bind to this specimen, or
    ``None`` for Visiting mode — there the catalogNumber belongs to a host
    collection, so no code is assigned; only the biological associations (if any)
    are saved.

    `queue_labels` controls the print queue:
      - Digitize standard: ``False`` — the identifier label is pre-printed in a
        batch and pinned by hand, and the specimen already carries its own data
        labels, so nothing is queued (the code is still bound).
      - Mounting: ``True`` — these are freshly mounted specimens that need a whole
        sheet printed, so the identifier, data (occurrence) and determination
        labels are all queued. The identifier row sits with the data row so the
        pair can be matched while cutting the sheet (the print service interleaves
        identifier ↔ data per specimen).
      - Visiting: irrelevant (no ``code``, nothing queued).

    `print_group_id` / `source` tag the queued rows so the printed sheet groups
    them under one origin header (only used when `queue_labels`; allocate the id
    once per batch via ``print_queue.next_print_group_id``).

    `associations` is an iterable of ``{"rel_id", "taxon_id"}`` dicts — the
    specimen is the subject, the taxon the object. Returns the created
    BiologicalAssociation rows in input order (so callers can attach per-association
    extras such as staged media to the new ids); empty list when none.
    """
    if code is not None:
        lc = assign_code(session, code, collection_object_id)
        if queue_labels:
            enqueue_identifier(session, lc.id,
                               print_group_id=print_group_id, source=source)
            enqueue_data(session, collection_object_id,
                         print_group_id=print_group_id, source=source)
            enqueue_determination(session, collection_object_id,
                                  print_group_id=print_group_id, source=source)
    created = []
    for assoc in associations:
        ba = save_biological_association(
            session,
            collection_object_id=collection_object_id,
            biological_relationship_id=assoc["rel_id"],
            object_taxon_id=assoc["taxon_id"],
        )
        created.append(ba)
    return created


def update_collection_object(session: Session, co_id: int, **fields) -> CollectionObject:
    """Update mutable fields on a CollectionObject.

    catalog_number and institution_code are immutable. collection_code MAY change
    (a specimen can be re-homed to another collection when gifted), but is NOT NULL,
    so an attempt to blank it is rejected loudly rather than silently skipped.
    """
    co = session.get(CollectionObject, co_id)
    if co is None:
        raise ValueError(f"CollectionObject {co_id} not found")
    for attr, val in fields.items():
        if attr in ("catalog_number", "institution_code"):
            continue  # immutable
        if attr == "collection_code":
            if not val:  # NOT NULL — refuse to blank the namespace
                raise ValueError("collection_code cannot be blank (NOT NULL).")
            co.collection_code = val
            continue
        if val == "":
            val = None
        if attr == "individual_count" and val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        setattr(co, attr, val)
    co.updated_at = _utcnow()
    session.flush()
    return co


def delete_determination(session: Session, det_id: int) -> None:
    """Delete a determination by id. No-op if not found."""
    d = session.get(TaxonDetermination, det_id)
    if d:
        session.delete(d)
        session.flush()


def update_determination_metadata(
    session: Session,
    det_id: int,
    *,
    sex: str | None,
    type_status: str | None,
    identified_by_id: int | None,
    date_identified: str | None,
    identification_qualifier: str | None,
    identification_remarks: str | None,
) -> TaxonDetermination:
    """Update non-taxon metadata on an existing determination."""
    d = session.get(TaxonDetermination, det_id)
    if d is None:
        raise ValueError(f"TaxonDetermination {det_id} not found")
    d.sex                      = sex or None
    d.type_status              = type_status or None
    d.identified_by_id         = identified_by_id
    d.date_identified          = date_identified
    d.identification_qualifier = identification_qualifier
    d.identification_remarks   = identification_remarks
    d.updated_at               = _utcnow()
    session.flush()
    return d


def set_determination_as_current(
    session: Session,
    co_id: int,
    det_id: int,
) -> None:
    """Make det_id the sole current determination, retiring all others for this specimen."""
    now = _utcnow()
    (
        session.query(TaxonDetermination)
        .filter(TaxonDetermination.collection_object_id == co_id)
        .update({"is_current": 0, "updated_at": now})
    )
    (
        session.query(TaxonDetermination)
        .filter(TaxonDetermination.id == det_id)
        .update({"is_current": 1, "updated_at": now})
    )
    session.flush()


def retire_and_add_determination(
    session: Session,
    co_id: int,
    taxon_id: int,
    **fields,
) -> TaxonDetermination:
    """Retire all current determinations (is_current → 0), create a new current one."""
    now = _utcnow()
    (
        session.query(TaxonDetermination)
        .filter(
            TaxonDetermination.collection_object_id == co_id,
            TaxonDetermination.is_current == 1,
        )
        .update({"is_current": 0, "updated_at": now})
    )
    return create_determination(
        session, collection_object_id=co_id, taxon_id=taxon_id, is_current=1, **fields
    )


def get_determination_history(session: Session, co_id: int) -> list[TaxonDetermination]:
    """Return all determinations for a specimen, newest first."""
    return (
        session.query(TaxonDetermination)
        .filter(TaxonDetermination.collection_object_id == co_id)
        .order_by(TaxonDetermination.created_at.desc())
        .all()
    )


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
            collection_code=co.collection_code,
            scientific_name=format_scientific_name(t) if t else "",
            sex=td.sex if td else None,
            individual_count=co.individual_count,
            country=ce.country if ce else None,
            locality=(ce.locality or ce.verbatim_locality) if ce else None,
            event_date=(ce.event_date or ce.verbatim_event_date) if ce else None,
            recorded_by=ce.recorded_by_person.full_name if (ce and ce.recorded_by_person) else None,
            identified_by=td.identified_by_person.full_name if (td and td.identified_by_person) else None,
        )
        for co, td, ce, t in rows
    ]
