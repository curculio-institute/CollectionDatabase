"""Labels tab → "Locality labels" card, and the Records event sheet's dialog.

Queues N locality (data) labels for an existing collecting event, with no specimen
behind them — e.g. labels for material that is not mounted yet. The write is
`print_queue.enqueue_event_data`; both surfaces go through it.
"""
from __future__ import annotations

from nicegui import ui

import app.services as svc
import app.services.print_queue as pq_svc


def _queue(session_factory, event_id: int, raw_count) -> int | None:
    """Queue the labels; notify and return None on bad input or failure."""
    if raw_count is None or float(raw_count) != int(raw_count):
        ui.notify("Number of labels must be a whole number.", type="warning")
        return None
    n = int(raw_count)
    try:
        with session_factory() as s:
            with s.begin():
                pq_svc.enqueue_event_data(s, collecting_event_id=event_id, count=n)
    except Exception as exc:
        ui.notify(f"Failed: {exc}", type="negative")
        return None
    return n


def build_event_locality_labels_card(session_factory, *, on_queued=None) -> None:
    def _opts() -> dict:
        with session_factory() as s:
            return {o.id: o.summary for o in svc.search_collecting_events(s, "")}

    with ui.card().classes("w-full shadow-sm"):
        ui.label("Locality labels").classes("section-label mb-2")
        ui.label(
            "Print locality labels for an existing collecting event, without a specimen "
            "record — e.g. for material that is not mounted yet. Nothing is recorded."
        ).classes("text-sm mb-4").style("color:var(--tp-base-soft)")

        event_sel = (
            ui.select(options=_opts(), with_input=True, clearable=True,
                      label="Search existing events…")
            .classes("w-full")
            .tooltip("Type any locality, date, or collector name")
        )
        # DB-backed select: keep it live (CLAUDE.md UI conventions).
        ui.timer(2.0, lambda: event_sel.set_options(_opts()))

        with ui.row().classes("mt-4 gap-4 items-center"):
            count = ui.number("Number of labels", value=1, min=1,
                              max=pq_svc.MAX_LOCALITY_LABELS, step=1).classes("w-40")
            add_btn = ui.button("Add to print queue", icon="queue")
            status = ui.label("").classes("text-sm").style("color:var(--tp-base-soft)")

        def _add():
            if event_sel.value is None:
                ui.notify("Select an event first.", type="warning")
                return
            n = _queue(session_factory, event_sel.value, count.value)
            if n is None:
                return
            status.set_text(f"✓ {n} label(s) added to the print queue")
            if on_queued:
                on_queued()

        add_btn.on_click(_add)


def open_event_locality_dialog(session_factory, event_id: int, *, on_queued=None) -> None:
    """Records event sheet: ask how many locality labels, then queue them."""
    with ui.dialog() as dlg, ui.card().classes("p-4 gap-3"):
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        ui.label("Print locality labels").classes("section-label")
        count = ui.number("Number of labels", value=1, min=1,
                          max=pq_svc.MAX_LOCALITY_LABELS, step=1).classes("w-40")

        def _add():
            n = _queue(session_factory, event_id, count.value)
            if n is None:
                return
            dlg.close()
            ui.notify(f"Queued {n} locality label{'s' if n != 1 else ''} — "
                      f"print them from the Labels tab.", type="positive")
            if on_queued:
                on_queued()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Add to print queue", icon="queue", on_click=_add).classes("btn-save")
    dlg.open()
