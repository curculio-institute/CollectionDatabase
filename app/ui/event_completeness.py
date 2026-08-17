"""Shared "this event looks incomplete — save anyway?" confirmation (#173).

One dialog for every specimen-creation path (Digitize standard/visiting, Mounting
Session, Import & Assign) so the wording and the fields it checks can never drift
between them. Soft by design: none of coordinates / municipality / date / recordedBy
is schema-required, so this only confirms — it never blocks a save.
"""
from __future__ import annotations

from nicegui import ui

from app.services.validation import missing_event_fields


async def confirm_incomplete_event(fields: dict, *, recorded_by: bool) -> bool:
    """Return True if the save should proceed.

    True immediately (no dialog) when nothing is missing. Otherwise shows a Cancel /
    Save anyway dialog listing what's missing and awaits the user's choice.
    """
    missing = missing_event_fields(fields, recorded_by=recorded_by)
    if not missing:
        return True

    with ui.dialog() as dlg, ui.card():
        ui.label("Event is missing " + ", ".join(missing)).classes("text-lg font-medium")
        ui.label(
            "These fields help geocoding, printed labels, and the TaxonWorks export. "
            "Save anyway?"
        ).classes("text-sm").style("color:var(--tp-base-soft)")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat")
            ui.button("Save anyway", on_click=lambda: dlg.submit(True)) \
                .props("color=warning")
    proceed = await dlg
    dlg.delete()   # per-action dialog — delete to avoid a timer leak
    return bool(proceed)
