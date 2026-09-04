"""Shared "this event looks incomplete — save anyway?" confirmation (#173).

One dialog for every specimen-creation path (Digitize standard/visiting, Mounting
Session, Import & Assign) so the wording and the fields it checks can never drift
between them. Soft by design: none of coordinates / municipality / date / recordedBy
is schema-required, so this only confirms — it never blocks a save.
"""
from __future__ import annotations

from app.services.validation import missing_event_fields
from app.ui.confirm_dialog import confirm


async def confirm_incomplete_event(fields: dict, *, recorded_by: bool) -> bool:
    """Return True if the save should proceed.

    True immediately (no dialog) when nothing is missing. Otherwise shows a Cancel /
    Save anyway dialog listing what's missing and awaits the user's choice.
    """
    missing = missing_event_fields(fields, recorded_by=recorded_by)
    if not missing:
        return True

    return await confirm(
        title="Event is missing " + ", ".join(missing),
        body="These fields help geocoding, printed labels, and the TaxonWorks "
             "export. Save anyway?",
        action_label="Save anyway",
        action_color="warning",
    )
