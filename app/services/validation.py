"""Cross-tab validation helpers.

Shared field validation so every save path (Digitize standard, Mounting Session,
…) enforces the same DB-level invariants up front with a friendly message,
rather than letting a CHECK constraint fail mid-transaction.
"""
from __future__ import annotations


def validate_event_fields(fields: dict) -> str | None:
    """Validate a collecting-event field dict (as built by ``_collect_event_fields``).

    Checks the values that have DB CHECK constraints — countryCode length and
    coordinate / uncertainty bounds — and returns a human-readable error string,
    or None if everything is in range. Empty values are allowed (optional fields).
    """
    cc = (fields.get("country_iso") or "").strip()
    if cc and len(cc) != 2:
        return "countryCode must be exactly 2 characters (or empty)."

    for label, key, lo, hi in [
        ("latitude",  "decimal_latitude",  -90,  90),
        ("longitude", "decimal_longitude", -180, 180),
    ]:
        val = fields.get(key)
        if val:
            try:
                f = float(val)
                if not (lo <= f <= hi):
                    return f"{label} out of range [{lo}, {hi}]."
            except ValueError:
                return f"{label} must be a number."

    uncert = fields.get("coordinate_uncertainty_in_meters")
    if uncert:
        try:
            if float(uncert) < 0:
                return "coordinateUncertainty must be ≥ 0."
        except ValueError:
            return "coordinateUncertainty must be a number."

    return None


# Fields #173 calls out as expected on every collecting event. None of these are
# schema-required (a real historical specimen may legitimately lack coordinates), so
# this is a SOFT completeness check — unlike validate_event_fields above, a caller
# shows the result as a "Save anyway?" confirmation, never a hard block.
MISSING_FIELD_LABELS = ("Coordinates", "Municipality", "Date", "Recorded by")


def missing_event_fields(fields: dict, *, recorded_by: bool) -> list[str]:
    """Return the labels (a subset of MISSING_FIELD_LABELS) of fields left empty on
    *fields* (a collecting-event field dict, as built by ``_collect_fields`` /
    ``_collect_event_fields`` / ``dwc_import.row_to_event_fields``).

    *recorded_by* is passed separately (a plain presence flag) because recordedBy is
    never itself in that dict — every save path resolves it to a person id inside its
    own transaction, so callers pass whatever they already have (a typed name, an
    already-resolved id) rather than this function re-deriving it.

    Coordinates count as present only when BOTH latitude and longitude are set — a
    lone one is not a usable point.
    """
    missing = []
    lat, lon = fields.get("decimal_latitude"), fields.get("decimal_longitude")
    if not (lat not in (None, "") and lon not in (None, "")):
        missing.append("Coordinates")
    if not (fields.get("municipality") or "").strip():
        missing.append("Municipality")
    if not (fields.get("event_date") or "").strip():
        missing.append("Date")
    if not recorded_by:
        missing.append("Recorded by")
    return missing
