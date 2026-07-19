from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CollectionObject,
    CollectingEvent,
    Taxon,
    TaxonDetermination,
)
from app.models.base import _utcnow
import app.services.biological as bio_svc
from app.services.biological import (
    save_biological_association, save_association_as_field_occurrence)
from app.services.events import create_collecting_event, get_or_create_exact_event
from app.services.identifiers import assign_code
from app.services.print_queue import (
    enqueue_data,
    enqueue_determination,
    enqueue_identifier,
)
from app.services.taxa import format_scientific_name
from app.services.label_text import format_place


@dataclass(frozen=True)
class RecentRow:
    collection_object_id: int
    catalog_number: str
    collection_code: str
    scientific_name: str       # composed name, WITHOUT authorship
    authorship: str | None     # kept separate so callers can style it (upright)
    taxon_rank: str | None     # italics are a function of rank — see taxa.scientific_name_html
    # (relationship, name, rank) per biological association — "collected from Quercus robur".
    hosts: list                # list[tuple[str, str, str | None]]
    confidential: bool         # this specimen is withheld from export
    event_confidential: bool   # …or inherits it from a confidential event (withholds them all)
    sex: str | None
    individual_count: int | None
    country: str | None
    locality: str | None
    place: str                 # Country: state, municipality, locality (format_place)
    event_date: str | None
    recorded_by: str | None
    identified_by: str | None
    date_identified: str | None


def create_collection_object(
    session: Session,
    *,
    collecting_event_id: int | None,
    catalog_number: str,
    repository_id: int,
    **fields,
) -> CollectionObject:
    co = CollectionObject(
        collecting_event_id=collecting_event_id,
        catalog_number=catalog_number,
        repository_id=repository_id,
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
    reuse_event: bool = False,
) -> CollectionObject:
    """Orchestrator: create/reuse event, then specimen, then determination in
    one transaction. Caller owns session.begin().

    ``reuse_event`` (Import & Assign): when no ``event_id`` is given, reuse an
    existing collecting_event that is 100% identical to ``event_fields`` instead of
    always inserting a new one (`get_or_create_exact_event`), so a batch sharing a
    collecting event does not spawn one identical event row per specimen. Off by
    default — other paths keep creating fresh events."""
    if event_id is None:
        if reuse_event:
            ce, _ = get_or_create_exact_event(session, **event_fields)
        else:
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

    `associations` is an iterable of ``{"rel_id", "taxon_id", "qualifier"?}`` dicts — the
    specimen is the subject; the object taxon is always recorded as its own HumanObservation
    ``field_occurrence`` sharing this specimen's event (decided 2026-07-11), with only the
    optional open-nomenclature ``qualifier`` surfaced at data entry. Returns the created
    BiologicalAssociation rows in input order (so callers can attach per-association extras
    such as staged media to the new ids); empty list when none.
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
        ba = save_association_as_field_occurrence(
            session,
            collection_object_id=collection_object_id,
            biological_relationship_id=assoc["rel_id"],
            taxon_id=assoc["taxon_id"],
            identification_qualifier=assoc.get("qualifier"),
        )
        created.append(ba)
    return created


def update_collection_object(session: Session, co_id: int, **fields) -> CollectionObject:
    """Update mutable fields on a CollectionObject.

    catalog_number is immutable. repository_id MAY change — re-homing a specimen to
    another collection (gift/exchange) re-points the FK (the in-app equivalent of
    editing ownerInstitutionCode; #75). It is NOT NULL, so an attempt to blank it is
    rejected loudly rather than silently skipped.
    """
    co = session.get(CollectionObject, co_id)
    if co is None:
        raise ValueError(f"CollectionObject {co_id} not found")
    for attr, val in fields.items():
        if attr == "catalog_number":
            continue  # immutable
        if attr == "repository_id":
            if not val:  # NOT NULL — refuse to blank the owning collection
                raise ValueError("repository_id cannot be blank (NOT NULL).")
            co.repository_id = val
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


# Sentinel: "caller did not pass this argument" — distinct from an explicit None,
# which for verbatim_identification means "recompose from the taxon".
_UNSET = object()


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
    verbatim_identification=_UNSET,
) -> TaxonDetermination:
    """Update non-taxon metadata on an existing determination.

    ``verbatim_identification`` defaults to *unchanged* (the frozen name stands). When passed,
    it sets the name-as-used explicitly — this is how a distinct **verbatim identification** is
    recorded (the determination's `taxon_id` still points at the current concept for search/
    grouping/export, while the verbatim carries the name as it was used, e.g. `Carabus preslii
    pecoudellus Deuve, 1998`). A blank string clears it back to None; callers that want the
    auto-composed name recompose it themselves and pass the result."""
    d = session.get(TaxonDetermination, det_id)
    if d is None:
        raise ValueError(f"TaxonDetermination {det_id} not found")
    d.sex                      = sex or None
    d.type_status              = type_status or None
    d.identified_by_id         = identified_by_id
    d.date_identified          = date_identified
    d.identification_qualifier = identification_qualifier
    d.identification_remarks   = identification_remarks
    if verbatim_identification is not _UNSET:
        d.verbatim_identification = verbatim_identification or None
    d.updated_at               = _utcnow()
    session.flush()
    return d


def update_determination_taxon(
    session: Session,
    det_id: int,
    *,
    taxon_id: int,
    verbatim_identification=_UNSET,
) -> TaxonDetermination:
    """Re-point a determination at another taxon, **re-freezing the name** (#54).

    This is the *correction* of a mis-picked taxon, not a re-identification: a genuine
    re-identification is a new determination (add one, it becomes current) so the specimen
    keeps its determination history. Correcting a data-entry slip has no history worth
    keeping — the determination as recorded never happened.

    `dwc:verbatimIdentification` is recomposed from the newly chosen taxon. That does not
    breach the freeze contract (CLAUDE.md §2): the contract forbids a *later re-classification
    of the taxon* from rewriting a saved determination. Here the user deliberately says this
    determination names the wrong taxon, and leaving the old verbatim would make the row claim
    a name that does not match its own `taxon_id` — the row would lie about itself.

    `identifiedBy` / `dateIdentified` are untouched: who determined it, and when, has not
    changed just because the name was recorded wrongly.

    By default the verbatim is recomposed from the new taxon. A caller preserving an explicit
    **verbatim identification** passes ``verbatim_identification=`` (the name as used), which is
    kept instead of recomposing — so a corrected `taxon_id` does not overwrite it.
    """
    from app.services.taxa import compose_full_name

    d = session.get(TaxonDetermination, det_id)
    if d is None:
        raise ValueError(f"TaxonDetermination {det_id} not found")
    t = session.get(Taxon, taxon_id)
    if t is None:
        raise ValueError(f"Taxon {taxon_id} not found")
    d.taxon_id                = taxon_id
    d.verbatim_identification = (
        compose_full_name(session, t) if verbatim_identification is _UNSET
        else (verbatim_identification or None))
    d.updated_at              = _utcnow()
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
        .options(joinedload(CollectionObject.repository))
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
            collection_code=co.repository.collection_code,
            scientific_name=(t.scientific_name or "") if t else "",
            authorship=(t.scientific_name_authorship if t else None),
            taxon_rank=(t.taxon_rank if t else None),
            hosts=[h for a in co.subject_associations
                   if (h := bio_svc.association_host(session, a))],
            confidential=bool(co.confidential),
            event_confidential=bool(ce.confidential) if ce else False,
            sex=td.sex if td else None,
            individual_count=co.individual_count,
            country=(ce.country_obj.name if (ce and ce.country_obj) else None),
            locality=(ce.locality or ce.verbatim_locality) if ce else None,
            place=format_place(ce),
            event_date=(ce.event_date or ce.verbatim_event_date) if ce else None,
            recorded_by=ce.recorded_by_person.full_name if (ce and ce.recorded_by_person) else None,
            identified_by=td.identified_by_person.full_name if (td and td.identified_by_person) else None,
            date_identified=(td.date_identified if td else None),
        )
        for co, td, ce, t in rows
    ]
