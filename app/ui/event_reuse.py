"""Shared 'this collecting event is reused / shared' banner.

One place for the orange warning + Detach-&-copy affordance used by both the
Digitize tab (reusing an existing event, fields read-only) and the Records tab
(editing an event shared by several specimens). The detach *action* differs per
tab — Digitize just stops reusing (creates a new event on save), Records copies
and relinks an existing specimen's event — so it is passed in as a callback.
"""
from __future__ import annotations

from nicegui import ui


def build_event_share_banner(*, message: str, actions=()):
    """Render an orange 'this event is shared' warning row + action buttons.

    ``actions`` is an iterable of dicts ``{"label", "on_click", "icon"?,
    "primary"?}``. A ``primary`` action is rendered solid/prominent, the rest
    flat. Pass ``()`` for a bare notice with no buttons. Returns the row element
    so the caller can show/hide or clear it.

    A handler may take the click event and call ``e.sender.disable()`` to retire
    its own button (e.g. an "Edit all" unlock that should only fire once).
    """
    with ui.row().classes("items-center gap-3 mb-3 w-full") as row:
        ui.icon("warning", size="sm").style("color:var(--tp-warning, #f59e0b)")
        ui.label(message).classes("text-sm").style("color:var(--tp-warning, #f59e0b)")
        ui.space()
        for a in actions:
            btn = ui.button(a["label"], icon=a.get("icon", "fork_right"),
                            on_click=a["on_click"])
            btn.props("no-caps color=warning "
                      + ("unelevated" if a.get("primary") else "flat"))
    return row
