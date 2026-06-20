"""Print queue — stage labels for batch printing.

Labels accumulate as specimens are digitized or identifications/identifiers are added.
Call build_pdf() to render everything queued, then clear_queue() once printed.

Three label types:
  'data'          — locality label, sourced from collection_object → collecting_event
  'determination' — taxon label, sourced from collection_object → current determination
  'identifier'    — code + QR label, sourced from label_code
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models import PrintQueue, CollectionObject, TaxonDetermination, LabelCode
from app.models.base import _utcnow
from app.services import taxa as taxa_svc
from app.services.label_text import format_locality_label
import app.services.labels as lbl


# Origin headers printed above each group on the sheet.
SOURCE_MOUNTING    = "Mounting Session"
SOURCE_IDENTIFIERS = "New identifiers"
SOURCE_REPRINT     = "Reprint"


# ---------------------------------------------------------------------------
# Enqueueing
# ---------------------------------------------------------------------------
# Rows enqueued in one operation (one Mounting save, one batch of reserved
# codes, …) share a print_group_id and a `source` header, so the printed sheet
# can draw them as one group with corresponding labels kept adjacent. Allocate
# the id once with next_print_group_id() and pass it (plus a source string) to
# every enqueue_* call in that operation.

def next_print_group_id(session: Session) -> int:
    """Return a fresh print_group_id (max existing + 1; 1 on an empty queue)."""
    current = session.query(func.max(PrintQueue.print_group_id)).scalar()
    return (current or 0) + 1


def enqueue_data(
    session: Session, collection_object_id: int,
    *, print_group_id: int | None = None, source: str | None = None,
) -> None:
    session.add(PrintQueue(
        label_type="data",
        collection_object_id=collection_object_id,
        print_group_id=print_group_id, source=source,
        created_at=_utcnow(), updated_at=_utcnow(),
    ))

def enqueue_determination(
    session: Session, collection_object_id: int,
    *, print_group_id: int | None = None, source: str | None = None,
) -> None:
    session.add(PrintQueue(
        label_type="determination",
        collection_object_id=collection_object_id,
        print_group_id=print_group_id, source=source,
        created_at=_utcnow(), updated_at=_utcnow(),
    ))

def enqueue_identifier(
    session: Session, label_code_id: int,
    *, print_group_id: int | None = None, source: str | None = None,
) -> None:
    session.add(PrintQueue(
        label_type="identifier",
        label_code_id=label_code_id,
        print_group_id=print_group_id, source=source,
        created_at=_utcnow(), updated_at=_utcnow(),
    ))


# ---------------------------------------------------------------------------
# Queue contents
# ---------------------------------------------------------------------------

@dataclass
class QueueSummary:
    n_data: int
    n_determination: int
    n_identifier: int

    @property
    def total(self) -> int:
        return self.n_data + self.n_determination + self.n_identifier


def queue_summary(session: Session) -> QueueSummary:
    rows = session.query(PrintQueue).all()
    return QueueSummary(
        n_data          = sum(1 for r in rows if r.label_type == "data"),
        n_determination = sum(1 for r in rows if r.label_type == "determination"),
        n_identifier    = sum(1 for r in rows if r.label_type == "identifier"),
    )


def queue_preview_items(session: Session) -> list[dict]:
    """Return a human-readable summary list for the UI preview."""
    items = []
    for row in session.query(PrintQueue).order_by(PrintQueue.created_at).all():
        if row.label_type == "data" and row.collection_object:
            co = row.collection_object
            ev = co.collecting_event
            loc = ", ".join(p for p in [
                ev.country if ev else None,
                ev.state_province if ev else None,
                ev.locality if ev else None,
            ] if p) or "—"
            assoc_names = [
                ba.object_taxon.scientific_name
                for ba in co.subject_associations
                if ba.object_taxon
            ]
            label_text = format_locality_label(ev, assoc_names or None, html=False)
            items.append({
                "type": "data",
                "text": loc,
                "label_text": label_text,
                "id": row.id,
            })

        elif row.label_type == "determination" and row.collection_object:
            det = next((d for d in row.collection_object.determinations if d.is_current), None)
            name = taxa_svc.format_scientific_name(det.taxon) if det and det.taxon else "—"
            items.append({"type": "determination", "text": name, "label_text": name, "id": row.id})

        elif row.label_type == "identifier" and row.label_code:
            code = row.label_code.code
            items.append({"type": "identifier", "text": code, "label_text": code, "id": row.id})

    return items


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _co_to_data_label(
    co: CollectionObject,
    text_override: str | None = None,
) -> lbl.DataLabel:
    ev = co.collecting_event
    assoc_names = [
        ba.object_taxon.scientific_name
        for ba in co.subject_associations
        if ba.object_taxon
    ]
    return lbl.DataLabel(
        country                  = ev.country                         if ev else None,
        country_code             = ev.country_code                    if ev else None,
        state_province           = ev.state_province                  if ev else None,
        municipality             = ev.municipality                    if ev else None,
        county                   = ev.county                          if ev else None,
        locality                 = ev.locality                        if ev else None,
        verbatim_locality        = ev.verbatim_locality               if ev else None,
        latitude                 = ev.decimal_latitude                if ev else None,
        longitude                = ev.decimal_longitude               if ev else None,
        coordinate_uncertainty_m = ev.coordinate_uncertainty_in_meters if ev else None,
        elevation_min            = ev.minimum_elevation_in_meters     if ev else None,
        elevation_max            = ev.maximum_elevation_in_meters     if ev else None,
        event_date               = ev.event_date                      if ev else None,
        recorded_by              = ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None,
        habitat                  = ev.habitat                         if ev else None,
        associated_species       = assoc_names or None,
        text_override            = text_override,
    )


def _co_to_det_label(co: CollectionObject) -> lbl.DeterminationLabel | None:
    det = next((d for d in co.determinations if d.is_current), None)
    if not det or not det.taxon:
        return None
    t = det.taxon
    genus, subgenus, specific, infra = taxa_svc.parse_scientific_name(t.scientific_name or "")
    return lbl.DeterminationLabel(
        genus                 = genus,
        subgenus              = subgenus,
        specific_epithet      = specific,
        infraspecific_epithet = infra,
        authorship            = t.scientific_name_authorship,
        determiner            = det.identified_by_person.full_name if det.identified_by_person else None,
        year                  = (det.date_identified or "")[:4] or None,
        sex                   = det.sex,
    )


def queued_groups(
    session: Session,
    text_overrides: dict[int, str] | None = None,
) -> list[lbl.LabelGroup]:
    """Reconstruct the queue into print groups (one per queue addition).

    Rows are bucketed by `print_group_id` in enqueue order; within a bucket they
    become per-specimen columns so each identifier prints under its data label. A
    data/determination row joins its column by `collection_object_id`; an
    identifier row joins by its label code's `collection_object_id` (set at assign
    time), or stands alone if the code is reserved-but-unassigned (a pre-print
    batch). `text_overrides` maps print_queue.id → edited data-label text.
    """
    rows = session.query(PrintQueue).order_by(PrintQueue.created_at, PrintQueue.id).all()
    overrides = text_overrides or {}

    # Bucket rows by group, preserving first-seen (enqueue) order.
    buckets: "dict[object, dict]" = {}
    for row in rows:
        gkey = row.print_group_id  # may be None (legacy/ungrouped)
        bucket = buckets.setdefault(gkey, {"source": row.source, "columns": {}})
        columns = bucket["columns"]

        if row.label_type == "data" and row.collection_object:
            ckey = ("co", row.collection_object_id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.data = _co_to_data_label(row.collection_object, overrides.get(row.id))
        elif row.label_type == "determination" and row.collection_object:
            ckey = ("co", row.collection_object_id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.determination = _co_to_det_label(row.collection_object)
        elif row.label_type == "identifier" and row.label_code:
            lc = row.label_code
            # Align an assigned code under its specimen's data label; an
            # unassigned (reserved) code stands alone in its own column.
            ckey = ("co", lc.collection_object_id) if lc.collection_object_id else ("code", lc.id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.id_code = lc.code

    return [
        lbl.LabelGroup(source=b["source"], specimens=list(b["columns"].values()))
        for b in buckets.values()
    ]


def build_pdf(
    session: Session,
    text_overrides: dict[int, str] | None = None,
    printed_at: str | None = None,
) -> bytes:
    """Render all queued labels into a single grouped PDF (see `queued_groups`)."""
    groups = queued_groups(session, text_overrides)
    return lbl.grouped_sheet(groups, printed_at or _utcnow())


def clear_queue(session: Session) -> int:
    """Delete all queued entries, return count removed."""
    n = session.query(PrintQueue).delete()
    session.flush()
    return n


def remove_item(session: Session, queue_id: int) -> None:
    session.query(PrintQueue).filter(PrintQueue.id == queue_id).delete()
    session.flush()
