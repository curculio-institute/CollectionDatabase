"""Shared 'this collecting event is reused / shared' banner.

One place for the orange warning + Detach-&-copy affordance used by both the
Digitize tab (reusing an existing event, fields read-only) and the Records tab
(editing an event shared by several specimens). The detach *action* differs per
tab — Digitize just stops reusing (creates a new event on save), Records copies
and relinks an existing specimen's event — so it is passed in as a callback.
"""
from __future__ import annotations

from nicegui import ui


def build_event_share_banner(*, message: str, button_label: str, on_detach,
                             icon: str = "fork_right"):
    """Render an orange warning row + a prominent Detach-&-copy button.

    Returns the row element so the caller can show/hide or clear it.
    """
    with ui.row().classes("items-center gap-3 mb-3 w-full") as row:
        ui.icon("warning", size="sm").style("color:var(--tp-warning, #f59e0b)")
        ui.label(message).classes("text-sm").style("color:var(--tp-warning, #f59e0b)")
        ui.space()
        (
            ui.button(button_label, icon=icon, on_click=on_detach)
            # Solid, default size — deliberately larger/more prominent than the
            # old flat size=sm button (the affordance should stand out).
            .props("no-caps unelevated color=warning")
        )
    return row
