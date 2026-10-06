"""Print queue — stage labels for batch printing.

Labels accumulate as specimens are digitized or identifications/identifiers are added.
Call build_pdf() to render everything queued, then clear_queue() once printed.

Three label types:
  'data'          — locality label, sourced from collection_object → collecting_event
  'determination' — taxon label, sourced from collection_object → current determination,
                    OR a *plain* identification label with no specimen, whose content
                    sits on the queue row itself (Labels tab; migration 0071)
  'identifier'    — code + QR label, sourced from label_code
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models import PrintQueue, CollectionObject, Taxon, TaxonDetermination, LabelCode
from app.models.base import _utcnow
from app.services import taxa as taxa_svc
import app.services.labels as lbl


# Origin headers printed above each group on the sheet.
SOURCE_MOUNTING    = "Mounting Session"
SOURCE_IDENTIFIERS = "New identifiers"
SOURCE_REPRINT     = "Reprint"
SOURCE_IDENTIFICATIONS = "Identification labels"


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
    taxon_determination_id: int | None = None,
) -> None:
    # taxon_determination_id pins the row to a specific identification (Records
    # reprint, #38); None → the specimen's current determination (create paths).
    session.add(PrintQueue(
        label_type="determination",
        collection_object_id=collection_object_id,
        taxon_determination_id=taxon_determination_id,
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


def enqueue_plain_determinations(
    session: Session, *, taxon_id: int, count: int,
    identified_by_id: int | None = None, date_identified: str | None = None,
    type_status: str | None = None, identification_qualifier: str | None = None,
    sex: str | None = None,
) -> int:
    """Queue ``count`` plain identification labels — determination labels that belong
    to no specimen (Labels tab), to be pinned by hand. One row per physical label, all
    in one "Identification labels" group. Returns how many were added.

    Bad input is refused with a sentence rather than left to the DB CHECKs (which
    remain the backstop): same rules as a specimen's determination.
    """
    from app.services.specimens import _reject_interval
    from app.vocab import IDENTIFICATION_QUALIFIERS
    if count < 1:
        raise ValueError("Number of labels must be at least 1.")
    if session.get(Taxon, taxon_id) is None:
        raise ValueError(f"Taxon #{taxon_id} not found.")
    _reject_interval(date_identified)
    qualifier = identification_qualifier or None
    if qualifier is not None and qualifier not in IDENTIFICATION_QUALIFIERS:
        raise ValueError(f"Unknown identification qualifier {qualifier!r}.")
    gid = next_print_group_id(session)
    for _ in range(count):
        session.add(PrintQueue(
            label_type="determination",
            taxon_id=taxon_id,
            identified_by_id=identified_by_id,
            date_identified=date_identified or None,
            type_status=type_status or None,
            identification_qualifier=qualifier,
            sex=sex or None,
            print_group_id=gid, source=SOURCE_IDENTIFICATIONS,
            created_at=_utcnow(), updated_at=_utcnow(),
        ))
    session.flush()
    return count


def requeue_batch_identifiers(session: Session, batch_id: int) -> int:
    """Re-add a batch's reserved (unprinted/unassigned) identifier codes to the
    print queue, so codes are never lost — a cleared queue can always be rebuilt
    from the batch. Skips codes already queued (no duplicates). Returns how many
    were added."""
    from app.models import LabelCode
    reserved = (
        session.query(LabelCode)
        .filter(LabelCode.batch_id == batch_id, LabelCode.status == "reserved")
        .order_by(LabelCode.created_at)
        .all()
    )
    if not reserved:
        return 0
    already = {
        q.label_code_id for q in
        session.query(PrintQueue).filter(PrintQueue.label_type == "identifier").all()
    }
    todo = [c for c in reserved if c.id not in already]
    if not todo:
        return 0
    gid = next_print_group_id(session)
    for c in todo:
        enqueue_identifier(session, c.id, print_group_id=gid, source=SOURCE_IDENTIFIERS)
    session.flush()
    return len(todo)


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

def _co_to_data_label(
    session: Session, co: CollectionObject, text_override: str | None = None,
) -> lbl.DataLabel:
    # The host a specimen was collected from is a biological association; the current
    # model records it as a HumanObservation field occurrence, so its object is a
    # field_occurrence, not a direct object_taxon. Resolve through the single owner
    # (bio.association_host) — reading ba.object_taxon alone dropped every fo-backed
    # host from the printed data label.
    from app.services import biological as bio_svc
    ev = co.collecting_event
    assoc_names = [
        host[1]
        for ba in co.subject_associations
        if (host := bio_svc.association_host(session, ba)) and host[1]
    ]
    return lbl.DataLabel(
        text_override            = text_override,
        country                  = (ev.country_obj.name if ev and ev.country_obj else None),
        country_code             = (ev.country_obj.iso_code if ev and ev.country_obj else None),
        state_province           = (ev.state_province_obj.name if ev and ev.state_province_obj else None),
        state_province_code      = (ev.state_province_obj.iso_code if ev and ev.state_province_obj else None),
        municipality             = ev.municipality                    if ev else None,
        county                   = (ev.county_obj.name if ev and ev.county_obj else None),
        locality                 = ev.locality                        if ev else None,
        verbatim_locality        = ev.verbatim_locality               if ev else None,
        latitude                 = ev.decimal_latitude                if ev else None,
        longitude                = ev.decimal_longitude               if ev else None,
        coordinate_uncertainty_m = ev.coordinate_uncertainty_in_meters if ev else None,
        elevation_min            = ev.minimum_elevation_in_meters     if ev else None,
        elevation_max            = ev.maximum_elevation_in_meters     if ev else None,
        event_date               = ev.event_date                      if ev else None,
        recorded_by              = ev.recorded_by_person.full_name if (ev and ev.recorded_by_person) else None,
        habitat                  = (ev.habitat_obj.name if ev and ev.habitat_obj else None),
        sampling_protocol        = (ev.sampling_protocol_obj.name if ev and ev.sampling_protocol_obj else None),
        associated_species       = assoc_names or None,
    )


def _co_to_det_label(
    co: CollectionObject, text_override: str | None = None,
    det: TaxonDetermination | None = None,
) -> lbl.DeterminationLabel | None:
    """Render a specimen's determination label. ``det`` names WHICH identification to
    render (Records reprint, #38, where a specimen reprints *every* determination);
    when None, falls back to the specimen's *current* determination (every create
    path, unchanged)."""
    if det is None:
        det = next((d for d in co.determinations if d.is_current), None)
    if not det or not det.taxon:
        # No identification → no auto text. But an override is print-only text that does
        # not need a determination behind it (the user is writing the name by hand), and it used
        # to be dropped here, so a stored edit never reached the paper (#67). Carry it through.
        if lbl.canonical_override(text_override):
            return lbl.DeterminationLabel(text_override=text_override)
        return None
    return _det_label(det.taxon, det, text_override)


def _det_label(t: Taxon, src, text_override: str | None = None) -> lbl.DeterminationLabel:
    """Compose a determination label for taxon ``t``. ``src`` supplies the
    identification's own fields — a `TaxonDetermination`, or a plain-label queue row,
    which carries the same attribute names for exactly this reason."""
    genus, subgenus, specific, infra = taxa_svc.parse_scientific_name(t.scientific_name or "")
    return lbl.DeterminationLabel(
        text_override         = text_override,
        genus                 = genus,
        subgenus              = subgenus,
        specific_epithet      = specific,
        infraspecific_epithet = infra,
        authorship            = t.scientific_name_authorship,
        qualifier             = src.identification_qualifier,   # cf. / aff. / ?
        type_status           = src.type_status,                # Holotype, …
        determiner            = src.identified_by_person.full_name if src.identified_by_person else None,
        year                  = (src.date_identified or "")[:4] or None,
        sex                   = src.sex,
    )


def _is_det_row(row: PrintQueue) -> bool:
    """A renderable determination row: a specimen's, or a plain one (taxon on the row)."""
    return row.label_type == "determination" and bool(row.collection_object or row.taxon)


def _row_det_label(row: PrintQueue, *, override: bool = False) -> lbl.DeterminationLabel | None:
    """The determination label a queue row prints — THE one place that knows a row is
    either a specimen's determination or a plain label. ``override=False`` gives the
    auto label (identity / "edited == auto"); True applies the row's print-only edit."""
    ov = row.text_override if override else None
    if row.taxon is not None:
        return _det_label(row.taxon, row, ov)
    return _co_to_det_label(row.collection_object, ov, row.taxon_determination)


def _det_column_key(row: PrintQueue, taken: bool) -> tuple:
    """Sheet column for a determination row. A plain label always stands alone. A
    specimen's first determination fills its column's determination band; further ones
    (a reprint reproduces EVERY identification) each take their own column so they
    don't overwrite each other. ``taken``: that band is already filled."""
    if row.collection_object_id is None:
        return ("plain", row.id)
    if taken:
        return ("det", row.taxon_determination_id or row.id)
    return ("co", row.collection_object_id)


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
            col.data = _co_to_data_label(session, row.collection_object, row.text_override)
            # Preview-only metadata (ignored by the print PDF): row id + identity of
            # the AUTO text so the WYSIWYG preview can click-map and group identical
            # labels. Identity is the auto (override-independent) text, matching
            # preview_model / set_override_for_identical.
            col.data_qid = row.id
            col.data_ident = _ident(lbl.label_plaintext(
                _co_to_data_label(session, row.collection_object)))
            col.co_id = row.collection_object_id
        elif _is_det_row(row):
            co_key = ("co", row.collection_object_id)
            # A single-ID specimen (every create path) still renders as one column.
            ckey = _det_column_key(
                row, co_key in columns and columns[co_key].determination is not None)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.determination = _row_det_label(row, override=True)
            col.det_qid = row.id
            _auto_dl = _row_det_label(row)
            _auto = lbl.label_plaintext(_auto_dl) if _auto_dl else ""
            col.det_ident = _ident(_auto) if _auto else None
            col.co_id = row.collection_object_id
        elif row.label_type == "identifier" and row.label_code:
            lc = row.label_code
            # Align an assigned code under its specimen's data label; an
            # unassigned (reserved) code stands alone in its own column.
            ckey = ("co", lc.collection_object_id) if lc.collection_object_id else ("code", lc.id)
            col = columns.setdefault(ckey, lbl.SpecimenLabels())
            col.id_code = lc.code
            col.id_qid = row.id
            if lc.collection_object_id:
                col.co_id = lc.collection_object_id

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


def _row_auto_identity(session: Session, row: PrintQueue) -> str | None:
    """Identity of a data/determination row's AUTO label text (override-independent),
    so identical labels group together for hover-highlight and batch edit. None for
    identifier rows / rows with no renderable label."""
    if row.label_type == "data" and row.collection_object:
        return _ident(lbl.label_plaintext(_co_to_data_label(session, row.collection_object)))
    if _is_det_row(row):
        dl = _row_det_label(row)
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
            auto = lbl.label_plaintext(_co_to_data_label(session, co))
            dl = _co_to_data_label(session, co, row.text_override)
            col["data_auto"] = auto
            col["data"] = row.text_override if row.text_override is not None else auto
            col["data_html"] = lbl._data_inner_html(dl)
            col["data_auto_html"] = lbl.label_auto_html(dl)
            col["data_qid"] = row.id
            col["data_ident"] = _ident(auto)
            col["co_id"] = co.id
        elif _is_det_row(row):
            co_key = ("co", row.collection_object_id)
            # Same column rule as queued_groups (one shared owner), so the preview
            # matches the printed sheet.
            col = _col(_det_column_key(
                row, co_key in cols and cols[co_key]["det_qid"] is not None))
            dl = _row_det_label(row)
            dl_ov = _row_det_label(row, override=True)
            auto = lbl.label_plaintext(dl) if dl else ""
            col["det_auto"] = auto or "—"
            col["det"] = row.text_override if row.text_override is not None else (auto or "—")
            col["det_html"] = lbl._det_inner_html(dl_ov) if dl_ov else ""
            col["det_auto_html"] = lbl.label_auto_html(dl) if dl else ""
            col["det_qid"] = row.id
            # A row with no auto text has NO identity: it must not group with every other
            # determination-less row (they share only their emptiness), or editing one would
            # stamp that name onto all of them. It stays individually editable — see
            # set_override_for_identical. Previously this hashed the "—" placeholder, so the
            # preview grouped them and offered an edit the store then silently dropped (#67).
            col["det_ident"] = _ident(auto) if auto else None
            col["co_id"] = row.collection_object_id     # None for a plain label
        elif row.label_type == "identifier" and row.label_code:
            lc = row.label_code
            col = _col(("co", lc.collection_object_id) if lc.collection_object_id else ("code", lc.id))
            col["id_code"] = lc.code
            col["id_qid"] = row.id

    return [
        {"source": b["source"], "specimens": list(b["columns"].values())}
        for b in buckets.values()
    ]


def row_auto_html(session: Session, queue_id: int) -> str:
    """The composed, formatted auto HTML for a queued data/determination row —
    used to detect when an edit equals the auto text (→ clear the override)."""
    row = session.get(PrintQueue, queue_id)
    if row is None:
        return ""
    if row.label_type == "data" and row.collection_object:
        return lbl.label_auto_html(_co_to_data_label(session, row.collection_object))
    if _is_det_row(row):
        dl = _row_det_label(row)
        return lbl.label_auto_html(dl) if dl else ""
    return ""


def row_current_html(session: Session, queue_id: int) -> str:
    """The formatted HTML currently PRINTED for a data/determination row — the
    override if one is set, else the auto text. Seeds the larger label editor."""
    row = session.get(PrintQueue, queue_id)
    if row is None:
        return ""
    if row.label_type == "data" and row.collection_object:
        return lbl._data_inner_html(_co_to_data_label(session, row.collection_object, row.text_override))
    if _is_det_row(row):
        dl = _row_det_label(row, override=True)
        return lbl._det_inner_html(dl) if dl else ""
    return ""


def reprint_specimen(session: Session, co_id: int) -> QueueSummary:
    """Queue a full reprint of one already-saved specimen (#38): its data (locality)
    label, its identifier label, and one determination label per **identification**
    the specimen carries — not just the current one. All land in one group under the
    "Reprint" header, so they print adjacent and can be pruned individually in the
    Print-queue tab. Returns what was queued.

    Reuses the same three renderers as every other create path (no new label type);
    a determination row is pinned to its specific `taxon_determination` so each
    identification prints as its own label. Skips nothing silently — the identifier
    is only queued if the specimen has a bound code (a visiting-mode specimen has a
    foreign catalog number and no reserved code, so there is no identifier to reprint),
    and the data label only when the specimen has a collecting event.
    """
    co = session.get(CollectionObject, co_id)
    if co is None:
        raise ValueError(f"Specimen #{co_id} not found.")

    # All reprints share ONE "Reprint" group on the sheet: reuse the existing reprint
    # group if there is one, so reprinting several specimens tiles them side by side
    # under a single header rather than making a new group per click.
    existing = (
        session.query(PrintQueue.print_group_id)
        .filter(PrintQueue.source == SOURCE_REPRINT,
                PrintQueue.print_group_id.isnot(None))
        .order_by(PrintQueue.print_group_id.desc())
        .first()
    )
    gid = existing[0] if existing else next_print_group_id(session)
    n_data = n_id = n_det = 0

    if co.collecting_event_id:
        enqueue_data(session, co_id, print_group_id=gid, source=SOURCE_REPRINT)
        n_data = 1

    lc = (
        session.query(LabelCode)
        .filter(LabelCode.collection_object_id == co_id)
        .order_by(LabelCode.created_at)
        .first()
    )
    if lc is not None:
        enqueue_identifier(session, lc.id, print_group_id=gid, source=SOURCE_REPRINT)
        n_id = 1

    # Current identification first, so it fills the specimen's determination band and
    # the older ones spill into their own columns beside it.
    dets = (
        session.query(TaxonDetermination)
        .filter(TaxonDetermination.collection_object_id == co_id)
        .order_by(TaxonDetermination.is_current.desc(), TaxonDetermination.id)
        .all()
    )
    for d in dets:
        enqueue_determination(
            session, co_id, print_group_id=gid, source=SOURCE_REPRINT,
            taxon_determination_id=d.id,
        )
        n_det += 1

    session.flush()
    return QueueSummary(n_data=n_data, n_determination=n_det, n_identifier=n_id)


def specimen_in_queue(session: Session, co_id: int) -> bool:
    """True if any label for this specimen is already queued — its data/determination
    rows, or its identifier row via the code's collection_object. Drives the Records
    'Reprint' button's already-queued disabled state."""
    if (session.query(PrintQueue.id)
            .filter(PrintQueue.collection_object_id == co_id).first()):
        return True
    return bool(
        session.query(PrintQueue.id)
        .join(LabelCode, PrintQueue.label_code_id == LabelCode.id)
        .filter(LabelCode.collection_object_id == co_id).first()
    )


def remove_specimen(session: Session, co_id: int) -> int:
    """Remove every queued label belonging to one specimen (its data + determination
    rows, and its identifier row via the code's collection_object). Returns the count."""
    rows = session.query(PrintQueue).all()
    victims = [r for r in rows
               if r.collection_object_id == co_id
               or (r.label_code and r.label_code.collection_object_id == co_id)]
    for r in victims:
        session.delete(r)
    session.flush()
    return len(victims)


def set_override_for_identical(session: Session, queue_id: int, text: str | None) -> int:
    """Set a print-only override on the given row AND every other queued label
    that is identical to it (same type + same auto text — see _row_auto_identity).
    Editing one identical label thus edits them all. Empty/None clears (→ auto).
    Returns how many rows were updated.

    The value is **canonicalised on the way in** (#67): the editor hands us a contenteditable's
    innerHTML, already entity-encoded, so storing it raw and deciding how to read it at render
    time meant store and render could disagree — `R & D` came back as the literal `R &amp; D`.
    One sanitised form in the DB, rendering is a pass-through, and what printed is what the
    preview showed. Anything that reduces to nothing clears the override rather than storing a
    blank label.

    A row with **no auto text** (a determination label on a specimen with no current
    identification) has no identity to group by — but it is still individually editable, and its
    override is still stored and printed. It must NOT group: every such row would otherwise share
    one "identity" (their common emptiness) and editing one would stamp that name onto all of
    them, across different specimens.
    """
    row = session.get(PrintQueue, queue_id)
    if row is None or row.label_type == "identifier":
        return 0

    value = lbl.canonical_override(text)
    target = _row_auto_identity(session, row)

    if target is None:
        # No auto text → no group. Edit this row alone (previously: silently dropped).
        row.text_override = value
        row.updated_at = _utcnow()
        session.flush()
        return 1

    n = 0
    for r in session.query(PrintQueue).filter(PrintQueue.label_type == row.label_type).all():
        if _row_auto_identity(session, r) == target:
            r.text_override = value
            r.updated_at = _utcnow()
            n += 1
    session.flush()
    return n


def build_pdf(session: Session, printed_at: str | None = None) -> bytes:
    """Render all queued labels into a single grouped PDF (see `queued_groups`)."""
    from app.services import repositories as repo_svc
    from app.config import get_config
    groups = queued_groups(session)
    cfg = get_config()
    borders = {
        "data":          cfg.label_border_data,
        "determination": cfg.label_border_determination,
        "identifier":    cfg.label_border_identifier,
    }
    return lbl.grouped_sheet(
        groups, printed_at or _utcnow(), repo_svc.name_map(session), borders,
        backend="chromium", paper=cfg.paper_format)


def preview_sheet(session: Session, printed_at: str | None = None) -> str:
    """The queued sheet as an editable, scoped HTML fragment for the WYSIWYG preview
    — identical layout to `build_pdf` (same builder), so what is shown is what prints.
    Pair with `labels.preview_css(borders)` (injected once) for the styling."""
    from app.services import repositories as repo_svc
    from datetime import datetime
    groups = queued_groups(session)
    stamp = printed_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    return lbl.preview_html(groups, stamp, repo_svc.name_map(session))


def clear_queue(session: Session) -> int:
    """Delete all queued entries, return count removed."""
    n = session.query(PrintQueue).delete()
    session.flush()
    return n


def remove_item(session: Session, queue_id: int) -> None:
    session.query(PrintQueue).filter(PrintQueue.id == queue_id).delete()
    session.flush()
