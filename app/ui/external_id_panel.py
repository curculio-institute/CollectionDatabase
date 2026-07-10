"""External resource identifier button + popup (#49).

``build_external_id_button(...)`` renders a compact link-icon button (with a count badge)
that opens a popup to view/add external identifiers (e.g. an iNaturalist observation URL)
for one record. Like the media button it has two modes:

- **bound** (Records): writes straight to the DB via ``external_ids`` service.
- **staged** (Digitize): the record doesn't exist yet, so entries are held in an in-memory
  list and committed on Save via ``commit(session, target_id)``.

For a biological association an external identifier denotes the *other party* (an optional
addition to the taxon object).
"""
from __future__ import annotations

from typing import Callable, Optional

from nicegui import ui

import app.services.external_ids as ext_svc


def build_external_id_button(
    session_factory,
    *,
    target_kind: str,
    target_id_getter: Optional[Callable[[], Optional[int]]] = None,
    staged: bool = False,
    staged_store: Optional[list] = None,
    on_change: Optional[Callable[[], None]] = None,
    tooltip: str = "Resource identifiers",
    deferred: bool = False,
) -> dict:
    """`deferred` (Records): the record exists, but adds/deletes are staged until the
    card's Save calls commit(session, target_id). Without it a click wrote immediately,
    which contradicts the card's single "Save changes" button."""
    staged_items: list[dict] = staged_store if staged_store is not None else []
    # Deferred bookkeeping: ids of existing rows to delete, and values to create.
    _pending_delete: list[int] = []
    _pending_add: list[str] = []

    def _target_id() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    def _count() -> int:
        if staged:
            return len(staged_items)
        return len(_entries())

    btn = ui.button(icon="link", on_click=lambda: _open()).props("flat dense round") \
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
            return [{"key": i, "value": it["value"]} for i, it in enumerate(staged_items)]
        tid = _target_id()
        if tid is None:
            return []
        with session_factory() as s:
            rows = [{"key": e.id, "value": e.value}
                    for e in ext_svc.list_identifiers(s, target_kind=target_kind, target_id=tid)]
        if not deferred:
            return rows
        rows = [r for r in rows if r["key"] not in _pending_delete]
        rows += [{"key": ("new", i), "value": v} for i, v in enumerate(_pending_add)]
        return rows

    def _add(value: str):
        """Add one resource identifier — the user just pastes the URI."""
        value = (value or "").strip()
        if not value:
            ui.notify("Enter a resource identifier (URI).", type="warning")
            return
        if staged:
            staged_items.append({"value": value})
        elif deferred:
            _pending_add.append(value)          # written by commit(), inside the card's Save
        else:
            tid = _target_id()
            if tid is None:
                ui.notify("Save the record first.", type="warning")
                return
            with session_factory() as s:
                with s.begin():
                    ext_svc.add_identifier(s, target_kind=target_kind, target_id=tid, value=value)
        _rebuild()
        refresh()
        if on_change:
            on_change()

    def _delete(e: dict):
        if staged:
            staged_items.pop(e["key"])
        elif deferred:
            key = e["key"]
            if isinstance(key, tuple):          # an entry added in this session
                _pending_add.pop(key[1])
            else:
                _pending_delete.append(key)     # existing row: deleted by commit()
        else:
            with session_factory() as s:
                with s.begin():
                    ext_svc.delete_identifier(s, e["key"])
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
                ui.label("No resource identifiers yet.").classes("text-sm italic") \
                    .style("color:var(--tp-base-soft)")
            for it in items:
                with ui.row().classes("items-center gap-2 w-full"):
                    val = it["value"]
                    if val.lower().startswith(("http://", "https://")):
                        ui.link(val, val, new_tab=True).classes("text-sm flex-1 truncate")
                    else:
                        ui.label(val).classes("text-sm flex-1 truncate")
                    ui.button(icon="delete", on_click=lambda e=it: _delete(e)) \
                        .props("flat dense round size=sm color=grey").tooltip("Remove")

    def _open():
        nonlocal rows
        with ui.dialog() as dlg, ui.card().classes("min-w-[480px] gap-2"):
            ui.label(tooltip).classes("text-base font-medium")
            rows = ui.column().classes("w-full gap-1")
            _rebuild()
            ui.separator().classes("my-1")
            ui.label("Add a resource identifier").classes("text-sm font-medium")
            value_in = ui.input(
                "Resource identifier (URI)",
                placeholder="e.g. https://www.inaturalist.org/observations/12345",
            ).props("dense").classes("w-full")

            def _save_close():
                # Add the entered URI (if any), then close — the standard modal pattern.
                if (value_in.value or "").strip():
                    _add(value_in.value)
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
        """staged (Digitize): create the held entries on the new record.
        deferred (Records): apply the staged deletes + adds to the existing record."""
        if deferred:
            for eid in _pending_delete:
                ext_svc.delete_identifier(session, eid)
            _pending_delete.clear()
            for value in _pending_add:
                ext_svc.add_identifier(session, target_kind=target_kind,
                                       target_id=target_id, value=value)
            _pending_add.clear()
            return
        for it in staged_items:
            ext_svc.add_identifier(session, target_kind=target_kind, target_id=target_id,
                                   value=it["value"])

    def has_changes() -> bool:
        return bool(_pending_add or _pending_delete)

    def clear():
        staged_items.clear()
        refresh()

    refresh()
    return {
        "button": btn, "refresh": refresh, "has_changes": has_changes,
        "has_content": (lambda: len(staged_items) > 0) if staged else (lambda: _count() > 0),
        "commit": commit, "clear": clear, "staged_items": staged_items,
    }
