"""UI helper for Darwin Core date input fields.

Call attach_date_validation(inp) on any ui.input that holds a DwC date.
On blur, non-ISO values are normalised (e.g. European format → ISO 8601)
and invalid values produce a warning notification. Changes are signalled
with the standard auto_fix_high notification (see design.md §3).
"""
from __future__ import annotations

from datetime import date

from nicegui import ui

from app.services.dates import parse_dwc_date


def append_year_pin(inp, *, visible_when_empty: bool = True) -> None:
    """Add a push_pin button that inserts the current year into a DwC date input.

    The Tier-2 "insert current year" default for every dateIdentified field
    (see design.md → Auto-fill tiers). Shared so the standard, records, and
    mounting forms stay consistent.
    """
    with inp.add_slot("append"):
        btn = (
            ui.button("", icon="push_pin")
            .props("flat dense round size=xs")
            .tooltip("Insert current year")
            .on_click(lambda: inp.set_value(str(date.today().year)))
        )
        if visible_when_empty:
            btn.bind_visibility_from(inp, "value", lambda v: not v)

# Shared CSS for the .auto-changed animation.  Import and inject this in any
# widget that uses the auto_fix_high pulsing indicator.
AUTO_CHANGED_CSS = """
<style>
@keyframes auto-changed-pulse {
  0%, 100% { opacity: 1;    filter: drop-shadow(0 0 2px currentColor); }
  50%       { opacity: 0.5; filter: drop-shadow(0 0 7px currentColor); }
}
.auto-changed { animation: auto-changed-pulse 1.8s ease-in-out infinite; }
</style>
"""


_FORMAT_HINT = {
    False: "Expected: YYYY, YYYY-MM, YYYY-MM-DD, YYYY MM DD, or European DD.MM.YYYY / MM.YYYY.",
    True:  "Expected: YYYY-MM-DD, YYYY-MM-DD/YYYY-MM-DD, YYYY MM DD, or European equivalents.",
}


def attach_date_validation(
    inp: ui.input,
    *,
    allow_interval: bool = False,
    no_future: bool = False,
) -> None:
    """Validate and normalise *inp*.value on blur.

    - Valid ISO 8601: silent, no change.
    - Parseable non-ISO (European format, missing zero-padding, etc.):
      value replaced with ISO form; notification "Normalised: old → new".
    - Unparseable or constraint violated: field wiped, warning with format hint.

    allow_interval=True for eventDate, dateIdentified and life-stage dates
    (ISO 8601 intervals are valid DwC); False (default) for single-date-only fields.
    no_future=True rejects dates after today (use for dateIdentified).
    """
    hint = _FORMAT_HINT[allow_interval]

    def _on_blur(_):
        raw = (inp.value or "").strip()
        if not raw:
            return
        normalised, err = parse_dwc_date(raw, allow_interval=allow_interval, no_future=no_future)
        if err:
            inp.value = ""
            ui.notify(
                f"Invalid date removed — {err}  {hint}",
                icon="auto_fix_high",
                type="warning",
                timeout=8000,
            )
        elif normalised != raw:
            inp.value = normalised
            ui.notify(
                f"Normalised: {raw} → {normalised}",
                icon="auto_fix_high",
                type="info",
                timeout=4000,
            )

    inp.on("blur", _on_blur)
