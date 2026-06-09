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
from app.services.label_text import format_locality_label
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


def _parse_sci_name(
    name: str,
) -> tuple[str, str | None, str | None, str | None]:
    """Split a bare scientific name into (genus, subgenus, specific_epithet, infraspecific).

    Handles:  'Sitona'                         → ('Sitona', None, None, None)
              'Sitona lineatus'                 → ('Sitona', None, 'lineatus', None)
              'Sitona (Sitona) lineatus'        → ('Sitona', 'Sitona', 'lineatus', None)
              'Sitona lineatus lineatus'        → ('Sitona', None, 'lineatus', 'lineatus')
              'Sitona (Sitona) lineatus allii'  → ('Sitona', 'Sitona', 'lineatus', 'allii')
    """
    parts = name.split()
    if len(parts) == 1:
        return parts[0], None, None, None
    genus = parts[0]
    if len(parts) >= 3 and parts[1].startswith("("):
        subgenus = parts[1].strip("()")
        specific = parts[2]
        infra = parts[3] if len(parts) > 3 else None
        return genus, subgenus, specific, infra
    specific = parts[1]
    infra = parts[2] if len(parts) > 2 else None
    return genus, None, specific, infra


def _co_to_det_label(co: CollectionObject) -> lbl.DeterminationLabel | None:
    det = next((d for d in co.determinations if d.is_current), None)
    if not det or not det.taxon:
        return None
    t = det.taxon
    genus, subgenus, specific, infra = _parse_sci_name(t.scientific_name or "")
    return lbl.DeterminationLabel(
        genus                 = genus,
        subgenus              = subgenus,
        specific_epithet      = specific,
        infraspecific_epithet = infra,
        authorship            = t.scientific_name_authorship,
        determiner            = det.identified_by_person.full_name if det.identified_by_person else None,
        year                  = (det.date_identified or "")[:4] or None,
    )


def build_pdf(
    session: Session,
    text_overrides: dict[int, str] | None = None,
) -> bytes:
    """Render all queued labels into a single combined PDF.

    text_overrides maps print_queue.id → plain-text label string for data labels
    that the user has manually edited in the UI before printing.
    """
    rows = session.query(PrintQueue).order_by(PrintQueue.label_type, PrintQueue.created_at).all()
    overrides = text_overrides or {}

    data_labels: list[lbl.DataLabel]            = []
    det_labels:  list[lbl.DeterminationLabel]   = []
    id_codes:    list[str]                       = []

    for row in rows:
        if row.label_type == "data" and row.collection_object:
            data_labels.append(
                _co_to_data_label(row.collection_object, overrides.get(row.id))
            )
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
