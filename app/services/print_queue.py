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

from app.models import PrintQueue, CollectionObject, TaxonDetermination, LabelCode
from app.models.base import _utcnow
from app.services import taxa as taxa_svc
import app.services.labels as lbl


# ---------------------------------------------------------------------------
# Enqueueing
# ---------------------------------------------------------------------------

def enqueue_data(session: Session, collection_object_id: int) -> None:
    session.add(PrintQueue(
        label_type="data",
        collection_object_id=collection_object_id,
        created_at=_utcnow(), updated_at=_utcnow(),
    ))

def enqueue_determination(session: Session, collection_object_id: int) -> None:
    session.add(PrintQueue(
        label_type="determination",
        collection_object_id=collection_object_id,
        created_at=_utcnow(), updated_at=_utcnow(),
    ))

def enqueue_identifier(session: Session, label_code_id: int) -> None:
    session.add(PrintQueue(
        label_type="identifier",
        label_code_id=label_code_id,
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
            ev = row.collection_object.collecting_event
            loc = ", ".join(p for p in [
                ev.country if ev else None,
                ev.state_province if ev else None,
                ev.locality if ev else None,
            ] if p) or "—"
            items.append({"type": "data", "text": loc, "id": row.id})

        elif row.label_type == "determination" and row.collection_object:
            det = next((d for d in row.collection_object.determinations if d.is_current), None)
            name = taxa_svc.format_scientific_name(det.taxon) if det and det.taxon else "—"
            items.append({"type": "determination", "text": name, "id": row.id})

        elif row.label_type == "identifier" and row.label_code:
            items.append({"type": "identifier", "text": row.label_code.code, "id": row.id})

    return items


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _co_to_data_label(co: CollectionObject) -> lbl.DataLabel:
    ev = co.collecting_event
    return lbl.DataLabel(
        country           = ev.country            if ev else None,
        state_province    = ev.state_province     if ev else None,
        county            = ev.county             if ev else None,
        locality          = ev.locality           if ev else None,
        verbatim_locality = ev.verbatim_locality  if ev else None,
        latitude          = ev.decimal_latitude   if ev else None,
        longitude         = ev.decimal_longitude  if ev else None,
        elevation_min     = ev.minimum_elevation_in_meters if ev else None,
        elevation_max     = ev.maximum_elevation_in_meters if ev else None,
        event_date        = ev.event_date         if ev else None,
        recorded_by       = ev.recorded_by        if ev else None,
        habitat           = ev.habitat            if ev else None,
    )


def _co_to_det_label(co: CollectionObject) -> lbl.DeterminationLabel | None:
    det = next((d for d in co.determinations if d.is_current), None)
    if not det or not det.taxon:
        return None
    t = det.taxon
    return lbl.DeterminationLabel(
        genus                 = t.genus,
        subgenus              = t.subgenus,
        specific_epithet      = t.specific_epithet,
        infraspecific_epithet = t.infraspecific_epithet,
        authorship            = t.scientific_name_authorship,
        determiner            = det.identified_by,
        year                  = (det.date_identified or "")[:4] or None,
    )


def build_pdf(session: Session) -> bytes:
    """Render all queued labels into a single combined PDF."""
    rows = session.query(PrintQueue).order_by(PrintQueue.label_type, PrintQueue.created_at).all()

    data_labels: list[lbl.DataLabel]            = []
    det_labels:  list[lbl.DeterminationLabel]   = []
    id_codes:    list[str]                       = []

    for row in rows:
        if row.label_type == "data" and row.collection_object:
            data_labels.append(_co_to_data_label(row.collection_object))
        elif row.label_type == "determination" and row.collection_object:
            dl = _co_to_det_label(row.collection_object)
            if dl:
                det_labels.append(dl)
        elif row.label_type == "identifier" and row.label_code:
            id_codes.append(row.label_code.code)

    return lbl.combined_sheet(data_labels, det_labels, id_codes)


def clear_queue(session: Session) -> int:
    """Delete all queued entries, return count removed."""
    n = session.query(PrintQueue).delete()
    session.flush()
    return n


def remove_item(session: Session, queue_id: int) -> None:
    session.query(PrintQueue).filter(PrintQueue.id == queue_id).delete()
    session.flush()
