"""Mounting Session specimen section.

Renders a 'Specimens to be labeled' card with a dynamic row table.
Codes are generated and labels are queued atomically on save.

Usage:
    ms_state = build_mounting_session_section(
        session_factory,
        collect_event_fields=lambda: _collect_event_fields(),
        commit_event=lambda s: ce["commit"](s),
        bio_state=bio_state,
        on_saved=lambda: _ms_on_saved(),
    )
    # ms_state["wipe"]() — clears all rows (call on mode toggle)
"""
from __future__ import annotations

from nicegui import ui

import app.services as svc
import app.services.identifiers as id_svc
import app.services.print_queue as pq_svc
import app.services.repositories as repo_svc
from app.config import get_config
from app.services.dates import parse_dwc_date
from app.services.validation import validate_event_fields
import app.services.person_defaults as pd_svc
from app.ui.date_input import attach_date_validation, append_year_pin
from app.ui.person_field import build_person_field
from app.ui.vocab_field import build_vocab_field
from app.services.vocabularies import preparation_vocab, disposition_vocab
from app.ui.taxon_search import build_taxon_search
from app.ui.type_status_field import build_type_status_field
from app.services.taxa import compose_scientific_name
from app.models import Taxon
# Controlled vocabularies — single source of truth (app/vocab.py).
from app.vocab import (
    LIFE_STAGE_OPTIONS as _LIFE_STAGE_OPTIONS,
    SEX_OPTIONS as _SEX_OPTIONS,
    NEW_SPECIMEN_DEFAULTS,
)


def _empty_row() -> dict:
    # preparations defaults to "pinned" here (mounting workflow); the rest of the
    # create defaults come from the shared NEW_SPECIMEN_DEFAULTS seed.
    return {
        "n":            NEW_SPECIMEN_DEFAULTS["individual_count"],
        "preparations": "pinned",
        "life_stage":   NEW_SPECIMEN_DEFAULTS["life_stage"],
        "det":          None,
    }


def build_mounting_session_section(
    session_factory,
    *,
    collect_event_fields,   # () -> dict (called at save time, not render time)
    commit_event,           # (session) -> {recorded_by_id, habitat_id, sampling_protocol_id}
    bio_state: dict,        # {"associations": [...]}
    on_saved,               # () -> None
    event_id_getter=lambda: None,  # () -> int | None: the selected/reused event,
                                   # so mounting links to it instead of creating a
                                   # duplicate (None → create one for the session)
) -> dict:
    """Render the Mounting Session specimen UI. Returns {"wipe": callable}."""

    rows: list[dict] = [_empty_row()]

    # ── identification dialog ────────────────────────────────────────────────

    def _open_det_dialog(row_idx: int) -> None:
        """Open a dialog to set (or change) the identification for rows[row_idx]."""
        prefill = rows[row_idx].get("det") or {}

        with ui.dialog().props("persistent") as dlg:
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            with ui.card().classes("w-full max-w-lg p-4"):
                ui.label("Set identification").classes("section-label mb-2")
                ui.separator().classes("mb-3")

                ts = build_taxon_search(
                    session_factory,
                    sources=("local", "taxonworks"),
                    placeholder="Enter genus or species name…",
                    initial_taxon_id=prefill.get("taxon_id"),
                    initial_label=prefill.get("taxon_label") or "",
                )

                def _default_idby() -> str | None:
                    with session_factory() as s:
                        return pd_svc.get_defaults(s)[0]

                with ui.row().classes("w-full items-center gap-1 mt-3"):
                    idby_state = build_person_field(
                        session_factory,
                        "identifiedBy",
                        default_fn=_default_idby,
                        initial_value=prefill.get("identified_by_name"),
                    )

                date_in = (
                    ui.input(
                        "dateIdentified",
                        placeholder="YYYY or YYYY-MM-DD",
                        value=prefill.get("date_identified") or "",
                    )
                    .classes("w-full mt-2")
                )
                append_year_pin(date_in)
                attach_date_validation(date_in, no_future=True)

                with ui.row().classes("w-full flex-wrap gap-2 mt-2"):
                    sex_sel = ui.select(
                        _SEX_OPTIONS, label="sex",
                        value=prefill.get("sex") or "",
                    ).classes("w-32")
                    type_state = build_type_status_field(
                        initial_value=prefill.get("type_status") or None,
                        classes="w-36",
                    )

                qual_in = (
                    ui.input(
                        "identificationQualifier",
                        placeholder="cf., aff., ?",
                        value=prefill.get("qualifier") or "",
                    )
                    .classes("w-full mt-2")
                )
                rem_in = (
                    ui.input(
                        "remarks",
                        value=prefill.get("remarks") or "",
                    )
                    .classes("w-full mt-2")
                )

                def _do_apply(to_all_below: bool) -> None:
                    tid = ts["taxon_id"]
                    if not tid:
                        ui.notify("Select a taxon first.", type="warning")
                        return
                    if tid == -1:
                        ui.notify("Taxon import still in progress — wait a moment.", type="warning")
                        return
                    # Normalise the date here (not only on the input's async blur),
                    # so a value typed-then-Apply'd before blur completes still lands
                    # as ISO in the DwC date column.
                    date_norm, date_err = parse_dwc_date(date_in.value or "", no_future=True)
                    if date_err:
                        ui.notify(f"dateIdentified: {date_err}", type="warning")
                        return
                    idby_id = None
                    if idby_state["get_value"]():
                        with session_factory() as s:
                            with s.begin():
                                idby_id = idby_state["commit"](s)
                    det = {
                        "taxon_id":           tid,
                        "taxon_label":        ts["label"],
                        "identified_by_id":   idby_id,
                        "identified_by_name": idby_state["get_value"](),
                        "date_identified":    date_norm or None,
                        "sex":                sex_sel.value or None,
                        "type_status":        type_state["get_value"]() or None,
                        "qualifier":          qual_in.value or None,
                        "remarks":            rem_in.value or None,
                    }
                    targets = range(row_idx, len(rows)) if to_all_below else range(row_idx, row_idx + 1)
                    for i in targets:
                        rows[i]["det"] = dict(det)
                    dlg.close()
                    _rebuild_table()

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    (
                        ui.button("Apply to all below", on_click=lambda: _do_apply(True))
                        .props("flat color=secondary")
                    )
                    ui.button("Apply", on_click=lambda: _do_apply(False)).classes("btn-save")

        dlg.open()

    # ── row table card ───────────────────────────────────────────────────────

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-3 mb-1"):
            ui.label("Specimens to be labeled").classes("section-label")
            ui.space()
            (
                ui.button("Add row", icon="add_circle_outline", on_click=lambda: _add_row())
                .props("flat color=secondary size=sm")
            )
        ui.separator().classes("mb-3")
        rows_col = ui.column().classes("w-full gap-0")

    # ── row manipulation ────────────────────────────────────────────────────

    def _add_row():
        rows.append(_empty_row())
        _rebuild_table()

    def _remove_row(idx: int):
        if len(rows) > 1:
            rows.pop(idx)
            _rebuild_table()

    def _copy_from_prev(idx: int):
        if idx > 0 and rows[idx - 1]["det"] is not None:
            rows[idx]["det"] = dict(rows[idx - 1]["det"])
            _rebuild_table()

    def _copy_det_to_all_below(from_idx: int):
        if rows[from_idx]["det"] is not None:
            for i in range(from_idx + 1, len(rows)):
                rows[i]["det"] = dict(rows[from_idx]["det"])
            _rebuild_table()

    def _rebuild_table():
        rows_col.clear()
        with rows_col:
            for i, row in enumerate(rows):
                with ui.row().classes("w-full items-center gap-2 flex-wrap py-1"):
                    # Row number
                    (
                        ui.label(f"{i + 1}.")
                        .classes("text-sm w-5 text-right shrink-0")
                        .style("color:var(--tp-base-soft)")
                    )
                    # Code placeholder — assigned at save time
                    (
                        ui.label("[auto]")
                        .classes("text-xs font-mono shrink-0")
                        .style("color:var(--tp-base-soft); min-width:5rem; text-align:center")
                        .tooltip("Code assigned on save")
                    )
                    # n
                    n_in = ui.number("n", value=row["n"], min=1, precision=0).classes("w-16")
                    n_in.on_value_change(lambda e, idx=i: rows[idx].update({"n": int(e.value or 1)}))
                    # preparations (controlled vocab — same dropdown as the main forms)
                    _prep_holder: dict = {}
                    def _on_prep(idx=i, h=_prep_holder):
                        rows[idx]["preparations"] = h["f"]["get_value"]() or ""
                    prep_f = build_vocab_field(
                        session_factory, preparation_vocab, "preps",
                        initial_value=row["preparations"] or None,
                        on_change=_on_prep, classes="w-28",
                    )
                    _prep_holder["f"] = prep_f
                    # lifeStage
                    ls_sel = ui.select(_LIFE_STAGE_OPTIONS, value=row["life_stage"], label="stage").classes("w-24")
                    ls_sel.on_value_change(lambda e, idx=i: rows[idx].update({"life_stage": e.value}))
                    # identification button
                    det = row["det"]
                    if det is None:
                        (
                            ui.button(
                                "Set identification", icon="add",
                                on_click=lambda _, idx=i: _open_det_dialog(idx),
                            )
                            .props("flat dense size=sm color=secondary")
                        )
                        # copy-from-previous shortcut
                        if i > 0 and rows[i - 1]["det"] is not None:
                            (
                                ui.button(
                                    "", icon="arrow_upward",
                                    on_click=lambda _, idx=i: _copy_from_prev(idx),
                                )
                                .props("flat dense round size=xs")
                                .tooltip("Copy identification from row above")
                            )
                    else:
                        with ui.column().classes("gap-0 shrink-0"):
                            (
                                ui.button(
                                    det["taxon_label"], icon="check_circle",
                                    on_click=lambda _, idx=i: _open_det_dialog(idx),
                                )
                                .props("flat dense size=sm color=positive")
                                .tooltip("Click to change identification")
                            )
                            _idby = det.get("identified_by_name")
                            _parts = [
                                p for p in [
                                    det.get("sex") or None,
                                    det.get("type_status") or None,
                                    det.get("qualifier") or None,
                                    f"det. {_idby}" if _idby else None,
                                    det.get("date_identified") or None,
                                ]
                                if p
                            ]
                            if _parts:
                                (
                                    ui.label(" · ".join(_parts))
                                    .classes("text-sm pl-2")
                                    .style("color:var(--tp-base-soft)")
                                )
                        # copy-to-all-below shortcut
                        if i < len(rows) - 1:
                            (
                                ui.button(
                                    "", icon="arrow_downward",
                                    on_click=lambda _, idx=i: _copy_det_to_all_below(idx),
                                )
                                .props("flat dense round size=xs")
                                .tooltip("Copy identification to all rows below")
                            )
                    ui.space()
                    # remove row
                    (
                        ui.button(
                            "", icon="close",
                            on_click=lambda _, idx=i: _remove_row(idx),
                        )
                        .props("flat dense round size=xs")
                        .style("color:var(--tp-base-soft)")
                    )

                if i < len(rows) - 1:
                    ui.separator().classes("my-0")

    _rebuild_table()

    # ── validation ──────────────────────────────────────────────────────────

    def _validate() -> str | None:
        cfg = get_config()
        if not cfg.institution_code:
            return "institutionCode not configured — open Settings."
        if not cfg.collection_code:
            return "collectionCode not configured — open Settings."
        if not rows:
            return "Add at least one specimen row."
        for i, row in enumerate(rows):
            if row["det"] is None:
                return f"Row {i + 1} has no identification — set it before saving."
        # Same event/coordinate checks as the standard Digitize path, so a malformed
        # shared collecting event fails with a friendly message up front rather than
        # a cryptic CHECK-constraint rollback after codes are reserved.
        return validate_event_fields(collect_event_fields())

    # ── save ────────────────────────────────────────────────────────────────

    def _do_save() -> None:
        err = _validate()
        if err:
            ui.notify(err, type="negative")
            return
        cfg = get_config()
        try:
            with session_factory() as s:
                with s.begin():
                    event_ids = commit_event(s)
                    ev_fields = {**collect_event_fields(), **event_ids}

                    # Generate all sequential codes in one batch
                    _batch_id, codes = id_svc.reserve_sequential_codes(
                        s, cfg.collection_code, len(rows)
                    )

                    # Own collection: resolve config's collection code → repository_id
                    # once for the whole session (#75).
                    repository_id = repo_svc.resolve_id(
                        s, collection_code=cfg.collection_code,
                        institution_code=cfg.institution_code,
                    )

                    # One print group for the whole session → the sheet prints
                    # these specimens together under a "Mounting Session" header.
                    group_id = pq_svc.next_print_group_id(s)

                    # Reuse the event selected in the Collecting Event card if any
                    # (don't duplicate it); else create one and share it across the
                    # session's specimens.
                    event_id: int | None = event_id_getter()
                    for row, code in zip(rows, codes):
                        det = row["det"]
                        # Freeze the determination name at save time.
                        _det_taxon = s.get(Taxon, det["taxon_id"])
                        verbatim = compose_scientific_name(s, _det_taxon) if _det_taxon else None
                        co = svc.save_specimen_entry(
                            s,
                            taxon_id=det["taxon_id"],
                            event_id=event_id,
                            event_fields=ev_fields,
                            specimen_fields={
                                "catalog_number":    code,
                                "repository_id":     repository_id,
                                "individual_count":  row["n"],
                                "preparation_id":    (preparation_vocab.get_or_create(s, row["preparations"]).id
                                                      if (row["preparations"] or "").strip() else None),
                                "life_stage":        row["life_stage"] or None,
                                "disposition_id":    disposition_vocab.get_or_create(
                                                         s, NEW_SPECIMEN_DEFAULTS["disposition"]).id,
                                "basis_of_record":   NEW_SPECIMEN_DEFAULTS["basis_of_record"],
                            },
                            determination_fields={
                                "sex":                      det.get("sex"),
                                "type_status":              det.get("type_status"),
                                "identified_by_id":         det["identified_by_id"],
                                "date_identified":          det["date_identified"],
                                "identification_qualifier": det["qualifier"],
                                "identification_remarks":   det["remarks"],
                                "verbatim_identification":  verbatim,
                            },
                        )
                        if event_id is None:
                            event_id = co.collecting_event_id

                        # Shared finalization seam (see finalize_specimen): bind the
                        # code and queue the full sheet — identifier + data
                        # (occurrence) + determination labels — so a freshly mounted
                        # specimen gets all its labels, with the identifier kept
                        # beside its data label for matching while cutting.
                        svc.finalize_specimen(
                            s,
                            collection_object_id=co.id,
                            code=code,
                            queue_labels=True,
                            print_group_id=group_id,
                            source=pq_svc.SOURCE_MOUNTING,
                            associations=bio_state["associations"],
                        )

            n = len(rows)

        except Exception as exc:
            ui.notify(f"Save failed: {exc}", type="negative")
            return

        ui.notify(
            f"Saved {n} specimen{'s' if n > 1 else ''} — labels queued for printing.",
            type="positive",
        )
        wipe()
        on_saved()

    # ── save bar ────────────────────────────────────────────────────────────

    with ui.row().classes("w-full items-center gap-4 px-1 mt-2"):
        ui.space()
        (
            ui.button("Save Specimens and Print labels", icon="print", on_click=_do_save)
            .classes("btn-save")
        )

    # ── public API ──────────────────────────────────────────────────────────

    def wipe() -> None:
        rows.clear()
        rows.append(_empty_row())
        _rebuild_table()

    def has_content() -> bool:
        """True if the staging table holds more than a single pristine row."""
        if len(rows) > 1:
            return True
        r = rows[0]
        return (
            r.get("det") is not None
            or r.get("n") != NEW_SPECIMEN_DEFAULTS["individual_count"]
            or (r.get("preparations") or "") != "pinned"
            or (r.get("life_stage") or None) != NEW_SPECIMEN_DEFAULTS["life_stage"]
        )

    return {"wipe": wipe, "has_content": has_content}
