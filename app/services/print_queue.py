"""Print queue — stage labels for batch printing.

Labels accumulate as specimens are digitized or identifications/identifiers are added.
Call build_pdf() to render everything queued, then clear_queue() once printed.

Three label types:
  'data'          — locality label, sourced from collection_object → collecting_event
  'determination' — taxon label, sourced from collection_object → current determination
  'identifier'    — code + QR label, sourced from label_code
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models import PrintQueue, CollectionObject, TaxonDetermination, LabelCode
from app.models.base import _utcnow
from app.services import taxa as taxa_svc
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


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _co_to_data_label(co: CollectionObject, text_override: str | None = None) -> lbl.DataLabel:
    ev = co.collecting_event
    assoc_names = [
        ba.object_taxon.scientific_name
        for ba in co.subject_associations
        if ba.object_taxon
    ]
    return lbl.DataLabel(
        text_override            = text_override,
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
    )


def _co_to_det_label(co: CollectionObject, text_override: str | None = None) -> lbl.DeterminationLabel | None:
    det = next((d for d in co.determinations if d.is_current), None)
    if not det or not det.taxon:
        return None
    t = det.taxon
    genus, subgenus, specific, infra = taxa_svc.parse_scientific_name(t.scientific_name or "")
    return lbl.DeterminationLabel(
        text_override         = text_override,
        genus                 = genus,
        subgenus              = subgenus,
        specific_epithet      = specific,
        infraspecific_epithet = infra,
        authorship            = t.scientific_name_authorship,
        qualifier             = det.identification_qualifier,   # cf. / aff. / ?
        type_status           = det.type_status,                # Holotype, …
        determiner            = det.identified_by_person.full_name if det.identified_by_person else None,
        year                  = (det.date_identified or "")[:4] or None,
        sex                   = det.sex,
    )


def queued_groups(session: Session) -> list[lbl.LabelGroup]:
    """Reconstruct the queue into print groups (one per queue addition).

    Rows are bucketed by `print_group_id` in enqueue order; within a bucket they
    become per-specimen columns so each identifier prints under its data label. A
    data/determination row joins its column by `collection_object_id`; an
    identifier row joins by its label code's `collection_object_id` (set at assign
    time), or stands alone if the code is reserved-but-unassigned (a pre-print
    batch). Labels are derived from the live records; a row's ``text_override``
    (a print-only edit typed in the queue, #37) replaces the rendered text.
    """
    rows = session.query(PrintQueue).order_by(PrintQueue.created_at, PrintQueue.id).all()

    # Bucket rows by group, preserving first-seen (enqueue) order.
    buckets: "dict[object, dict]" = {}
    for row in rows:
        gkey = row.print_group_id  # may be None (legacy/ungrouped)
        bucket = buckets.setdefault(gkey, {"source": row.source, "columns": {}})
        columns = bucket["columns"]

        if row.label_type == "data" and row.collection_object:
            ckey = ("co", row.collection_object_id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.data = _co_to_data_label(row.collection_object, row.text_override)
        elif row.label_type == "determination" and row.collection_object:
            ckey = ("co", row.collection_object_id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.determination = _co_to_det_label(row.collection_object, row.text_override)
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


def _ident(text: str | None) -> str:
    """Stable short identity key from a label's text. Two labels are 'identical'
    iff their auto-composed text matches — for a data label that means same
    collecting event AND same biological associations (the label is composed from
    both), independent of which event *row* or batch produced it (#37)."""
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:12]


def _row_auto_identity(row: PrintQueue) -> str | None:
    """Identity of a data/determination row's AUTO label text (override-independent),
    so identical labels group together for hover-highlight and batch edit. None for
    identifier rows / rows with no renderable label."""
    if row.label_type == "data" and row.collection_object:
        return _ident(lbl.label_plaintext(_co_to_data_label(row.collection_object)))
    if row.label_type == "determination" and row.collection_object:
        dl = _co_to_det_label(row.collection_object)
        return _ident(lbl.label_plaintext(dl)) if dl else None
    return None


def preview_model(session: Session) -> list[dict]:
    """Structured, editable preview of the queued sheet for the UI. Groups → per-
    specimen columns; each column carries, per label type, the queue row id, the
    printed text (override if set else auto), the auto text, the *formatted* HTML
    (printed + auto, for the WYSIWYG editor — keeps italics/bold, #45/#46), and an
    identity key (identical labels share it). Shape per specimen column::

        {co_id,
         data, data_auto, data_html, data_auto_html, data_qid, data_ident,
         det,  det_auto,  det_html,  det_auto_html,  det_qid,  det_ident,
         id_code}
    """
    rows = session.query(PrintQueue).order_by(PrintQueue.created_at, PrintQueue.id).all()
    buckets: "dict[object, dict]" = {}
    for row in rows:
        g = buckets.setdefault(row.print_group_id, {"source": row.source, "columns": {}})
        cols = g["columns"]

        def _col(key):
            return cols.setdefault(key, {
                "co_id": None,
                "data": None, "data_auto": None, "data_html": None,
                "data_auto_html": None, "data_qid": None, "data_ident": None,
                "det": None,  "det_auto": None,  "det_html": None,
                "det_auto_html": None,  "det_qid": None,  "det_ident": None,
                "id_code": None, "id_qid": None,
            })

        if row.label_type == "data" and row.collection_object:
            co = row.collection_object
            col = _col(("co", row.collection_object_id))
            auto = lbl.label_plaintext(_co_to_data_label(co))
            dl = _co_to_data_label(co, row.text_override)
            col["data_auto"] = auto
            col["data"] = row.text_override if row.text_override is not None else auto
            col["data_html"] = lbl._data_inner_html(dl)
            col["data_auto_html"] = lbl.label_auto_html(dl)
            col["data_qid"] = row.id
            col["data_ident"] = _ident(auto)
            col["co_id"] = co.id
        elif row.label_type == "determination" and row.collection_object:
            co = row.collection_object
            col = _col(("co", row.collection_object_id))
            dl = _co_to_det_label(co)
            dl_ov = _co_to_det_label(co, row.text_override)
            auto = lbl.label_plaintext(dl) if dl else "—"
            col["det_auto"] = auto
            col["det"] = row.text_override if row.text_override is not None else auto
            col["det_html"] = lbl._det_inner_html(dl_ov) if dl_ov else "—"
            col["det_auto_html"] = lbl.label_auto_html(dl_ov) if dl_ov else "—"
            col["det_qid"] = row.id
            col["det_ident"] = _ident(auto)
            col["co_id"] = co.id
        elif row.label_type == "identifier" and row.label_code:
            lc = row.label_code
            col = _col(("co", lc.collection_object_id) if lc.collection_object_id else ("code", lc.id))
            col["id_code"] = lc.code
            col["id_qid"] = row.id

    return [
        {"source": b["source"], "specimens": list(b["columns"].values())}
        for b in buckets.values()
    ]


def set_override_for_identical(session: Session, queue_id: int, text: str | None) -> int:
    """Set a print-only override on the given row AND every other queued label
    that is identical to it (same type + same auto text — see _row_auto_identity).
    Editing one identical label thus edits them all. Empty/None clears (→ auto).
    Returns how many rows were updated."""
    row = session.get(PrintQueue, queue_id)
    if row is None or row.label_type == "identifier":
        return 0
    target = _row_auto_identity(row)
    if target is None:
        return 0
    value = text or None
    n = 0
    for r in session.query(PrintQueue).filter(PrintQueue.label_type == row.label_type).all():
        if _row_auto_identity(r) == target:
            r.text_override = value
            r.updated_at = _utcnow()
            n += 1
    session.flush()
    return n


def build_pdf(session: Session, printed_at: str | None = None) -> bytes:
    """Render all queued labels into a single grouped PDF (see `queued_groups`)."""
    groups = queued_groups(session)
    return lbl.grouped_sheet(groups, printed_at or _utcnow())


def clear_queue(session: Session) -> int:
    """Delete all queued entries, return count removed."""
    n = session.query(PrintQueue).delete()
    session.flush()
    return n


def remove_item(session: Session, queue_id: int) -> None:
    session.query(PrintQueue).filter(PrintQueue.id == queue_id).delete()
    session.flush()
