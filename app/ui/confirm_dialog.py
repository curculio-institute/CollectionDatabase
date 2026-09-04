"""The one Cancel / <action> confirmation dialog shape, shared by every caller.

Before this existed, each caller (Digitize's mode-switch "Discard unsaved data?",
#173's "Save anyway?") hand-rolled its own near-identical dialog — same structure,
copy-pasted. One place now, so a future tweak (Escape-key handling, button props)
lands everywhere at once instead of drifting between copies.
"""
from __future__ import annotations

from nicegui import ui


async def confirm(*, title: str, body: str, action_label: str,
                   action_color: str = "negative") -> bool:
    """Show a Cancel / *action_label* dialog and return True iff the user chose to
    proceed. Deletes the dialog after resolving (per-action dialog — avoids a timer
    leak, see CLAUDE.md's NiceGUI dialog conventions)."""
    with ui.dialog() as dlg, ui.card():
        ui.label(title).classes("text-lg font-medium")
        ui.label(body).classes("text-sm").style("color:var(--tp-base-soft)")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat")
            ui.button(action_label, on_click=lambda: dlg.submit(True)) \
                .props(f"color={action_color}")
    proceed = await dlg
    dlg.delete()
    return bool(proceed)
