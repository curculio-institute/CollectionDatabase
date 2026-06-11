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
    cc = (fields.get("country_code") or "").strip()
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
