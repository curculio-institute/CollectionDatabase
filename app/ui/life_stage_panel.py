"""Reared-specimen life-stage history button + popup (#50).

``build_life_stage_button(...)`` renders a timeline-icon button (count badge) that opens a
popup to view/add life-stage rows for a reared specimen — each an earlier stage of the same
individual (e.g. the wild larva), recorded as (lifeStage, basisOfRecord, eventDate) without
duplicating the specimen/event. Bound mode (Records) writes to the DB; staged mode
(Digitize) holds rows in memory and commits on Save via ``commit(session, target_id)``.
"""
from __future__ import annotations

from typing import Callable, Optional

from nicegui import ui

import app.services.life_stage as ls_svc
from app.vocab import LIFE_STAGE_OPTIONS, BASIS_OPTIONS
from app.ui.date_input import attach_date_validation


def build_life_stage_button(
    session_factory,
    *,
    target_id_getter: Optional[Callable[[], Optional[int]]] = None,
    staged: bool = False,
    staged_store: Optional[list] = None,
    on_change: Optional[Callable[[], None]] = None,
    tooltip: str = "Rearing / life-stage history",
    deferred: bool = False,
) -> dict:
    """`deferred` (Records): the specimen exists, but adds/deletes are staged until the
    card's Save calls commit(session, target_id)."""
    staged_items: list[dict] = staged_store if staged_store is not None else []
    _pending_delete: list[int] = []
    _pending_add: list[dict] = []

    def _target_id() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    def _count() -> int:
        if staged:
            return len(staged_items)
        return len(_entries())

    btn = ui.button(icon="timeline", on_click=lambda: _open()).props("flat dense round") \
        .tooltip(tooltip)
    with btn:
        badge = ui.badge("0", color="secondary").props("floating")
    badge.set_visibility(False)

    def refresh():
        n = _count()
        badge.set_text(str(n))
        badge.set_visibility(n > 0)
        btn.props(f'color={"secondary" if n else "grey"}')

    def _entries() -> list[dict]:
        if staged:
            return [{"key": i, **it} for i, it in enumerate(staged_items)]
        tid = _target_id()
        if tid is None:
            return []
        with session_factory() as s:
            rows = [{"key": r.id, "life_stage": r.life_stage,
                     "basis_of_record": r.basis_of_record, "event_date": r.event_date or ""}
                    for r in ls_svc.list_life_stages(s, tid)]
        if not deferred:
            return rows
        rows = [r for r in rows if r["key"] not in _pending_delete]
        rows += [{"key": ("new", i), **it} for i, it in enumerate(_pending_add)]
        return rows

    def _add(life_stage: str, basis: str, event_date: str):
        if not (life_stage or "").strip():
            ui.notify("Choose a life stage.", type="warning")
            return
        if staged:
            staged_items.append({"life_stage": life_stage, "basis_of_record": basis,
                                 "event_date": event_date or ""})
        elif deferred:
            _pending_add.append({"life_stage": life_stage, "basis_of_record": basis,
                                 "event_date": event_date or ""})
        else:
            tid = _target_id()
            if tid is None:
                ui.notify("Save the record first.", type="warning")
                return
            with session_factory() as s:
                with s.begin():
                    ls_svc.add_life_stage(s, collection_object_id=tid, life_stage=life_stage,
                                          basis_of_record=basis, event_date=event_date or None)
        _rebuild()
        refresh()
        if on_change:
            on_change()

    def _delete(e: dict):
        if staged:
            staged_items.pop(e["key"])
        elif deferred:
            key = e["key"]
            if isinstance(key, tuple):
                _pending_add.pop(key[1])
            else:
                _pending_delete.append(key)
        else:
            with session_factory() as s:
                with s.begin():
                    ls_svc.delete_life_stage(s, e["key"])
        _rebuild()
        refresh()
        if on_change:
            on_change()

    rows = None

    def _rebuild():
        if rows is None:
            return
        rows.clear()
        items = _entries()
        with rows:
            if not items:
                ui.label("No life-stage history yet.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
            for it in items:
                with ui.row().classes("items-center gap-2 w-full"):
                    parts = [it["life_stage"], it["basis_of_record"]]
                    if it["event_date"]:
                        parts.append(it["event_date"])
                    ui.label(" · ".join(parts)).classes("text-sm flex-1")
                    ui.button(icon="delete", on_click=lambda e=it: _delete(e)) \
                        .props("flat dense round size=sm color=grey").tooltip("Remove")

    def _open():
        nonlocal rows
        with ui.dialog() as dlg, ui.card().classes("min-w-[480px] gap-2"):
            ui.label(tooltip).classes("text-base font-medium")
            ui.label("Earlier stages of this reared individual (e.g. the wild larva). The "
                     "locality comes from the specimen's collecting event.") \
                .classes("text-xs").style("color:var(--tp-base-soft)")
            rows = ui.column().classes("w-full gap-1")
            _rebuild()
            ui.separator().classes("my-1")
            ui.label("Add a life stage").classes("text-sm font-medium")
            with ui.row().classes("items-end gap-2 w-full"):
                stage_sel = ui.select(LIFE_STAGE_OPTIONS, label="lifeStage", value="larva") \
                    .props("dense").classes("w-32")
                basis_sel = ui.select(BASIS_OPTIONS, label="basisOfRecord",
                                      value="HumanObservation").props("dense").classes("w-48")
                date_in = ui.input("eventDate").props("dense").classes("flex-1")
                attach_date_validation(date_in, allow_interval=True)

            def _save_close():
                if (stage_sel.value or "").strip():
                    _add(stage_sel.value, basis_sel.value, date_in.value)
                dlg.close()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abort", on_click=dlg.close).props("flat")
                ui.button("Save & close", on_click=_save_close).props("color=secondary")
        dlg.on_value_change(lambda ev: (_sync_after_close() if not ev.value else None))
        dlg.open()

    def _sync_after_close():
        nonlocal rows
        rows = None
        refresh()

    def commit(session, target_id: int):
        """staged (Digitize): create held rows on the new specimen.
        deferred (Records): apply staged deletes + adds to the existing specimen."""
        items = staged_items
        if deferred:
            for rid in _pending_delete:
                ls_svc.delete_life_stage(session, rid)
            _pending_delete.clear()
            items = list(_pending_add)
            _pending_add.clear()
        for it in items:
            ls_svc.add_life_stage(session, collection_object_id=target_id,
                                  life_stage=it["life_stage"], basis_of_record=it["basis_of_record"],
                                  event_date=it["event_date"] or None)

    def has_changes() -> bool:
        return bool(_pending_add or _pending_delete)

    def clear():
        staged_items.clear()
        refresh()

    refresh()
    return {
        "has_changes": has_changes,
        "button": btn, "refresh": refresh,
        "has_content": (lambda: len(staged_items) > 0) if staged else (lambda: _count() > 0),
        "commit": commit, "clear": clear, "staged_items": staged_items,
    }
