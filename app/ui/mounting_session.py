"""Mounting Session specimen section.

Renders a 'Specimens to be labeled' card with a dynamic row table.
Codes are generated and labels are queued atomically on save.

Usage:
    ms_state = build_mounting_session_section(
        session_factory,
        collect_event_fields=lambda: _collect_event_fields(),
        commit_recby=lambda s: recby_state["commit"](s),
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
from app.config import get_config
from app.services.biological import save_biological_association
import app.services.person_defaults as pd_svc
from app.ui.date_input import attach_date_validation
from app.ui.person_field import build_person_field
from app.ui.taxon_search import build_taxon_search
from app.ui.type_status_field import build_type_status_field

_LIFE_STAGE_OPTIONS = ["adult", "larva", "pupa", "egg", ""]
_SEX_OPTIONS = ["male", "female", "undetermined", ""]


def _empty_row() -> dict:
    return {"n": 1, "preparations": "pinned", "life_stage": "adult", "det": None}


def build_mounting_session_section(
    session_factory,
    *,
    collect_event_fields,   # () -> dict (called at save time, not render time)
    commit_recby,           # (session) -> int | None
    bio_state: dict,        # {"associations": [...]}
    on_saved,               # () -> None
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
                        "date_identified":    date_in.value or None,
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
                    # preparations
                    preps_in = ui.input("preps", value=row["preparations"]).classes("w-28")
                    preps_in.on_value_change(lambda e, idx=i: rows[idx].update({"preparations": e.value}))
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
        return None

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
                    recby_id = commit_recby(s)
                    ev_fields = {**collect_event_fields(), "recorded_by_id": recby_id}

                    # Generate all sequential codes in one batch
                    _batch_id, codes = id_svc.reserve_sequential_codes(
                        s, cfg.collection_code, len(rows)
                    )

                    # Create specimens; reuse the same collecting event after the first
                    event_id: int | None = None
                    for row, code in zip(rows, codes):
                        det = row["det"]
                        co = svc.save_specimen_entry(
                            s,
                            taxon_id=det["taxon_id"],
                            event_id=event_id,
                            event_fields=ev_fields,
                            specimen_fields={
                                "catalog_number":    code,
                                "collection_code":   cfg.collection_code,
                                "institution_code":  cfg.institution_code,
                                "individual_count":  row["n"],
                                "preparations":      row["preparations"] or None,
                                "life_stage":        row["life_stage"] or None,
                                "disposition":       "in collection",
                                "basis_of_record":   "PreservedSpecimen",
                            },
                            determination_fields={
                                "sex":                      det.get("sex"),
                                "type_status":              det.get("type_status"),
                                "identified_by_id":         det["identified_by_id"],
                                "date_identified":          det["date_identified"],
                                "identification_qualifier": det["qualifier"],
                                "identification_remarks":   det["remarks"],
                            },
                        )
                        if event_id is None:
                            event_id = co.collecting_event_id

                        lc = id_svc.assign_code(s, code, co.id)

                        # Queue: identifier label → locality label → determination label
                        pq_svc.enqueue_identifier(s, lc.id)
                        pq_svc.enqueue_data(s, co.id)
                        pq_svc.enqueue_determination(s, co.id)

                        for assoc in bio_state["associations"]:
                            save_biological_association(
                                s,
                                collection_object_id=co.id,
                                biological_relationship_id=assoc["rel_id"],
                                object_taxon_id=assoc["taxon_id"],
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

    return {"wipe": wipe}
