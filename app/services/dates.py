"""Date parsing and normalisation for Darwin Core date fields.

DwC single dates (dateIdentified, etc.) accept:
    YYYY | YYYY-MM | YYYY-MM-DD

eventDate additionally accepts ISO 8601 intervals:
    YYYY-MM-DD/YYYY-MM-DD   (partial variants also valid: YYYY/YYYY)
"""
from __future__ import annotations

import re
from datetime import date as _date, datetime as _datetime

_ISO_YEAR       = re.compile(r'^\d{4}$')
_ISO_YEAR_MONTH = re.compile(r'^\d{4}-\d{1,2}$')
_ISO_FULL       = re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$')
_EU_FULL        = re.compile(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$')   # DD.MM.YYYY
_EU_MONTH_YEAR  = re.compile(r'^(\d{1,2})\.(\d{4})$')              # MM.YYYY


def _check_future(normalised: str) -> str | None:
    """Return an error string if *normalised* ISO date is in the future, else None."""
    today = _datetime.today().date()
    year = int(normalised[:4])
    if year > today.year:
        return f"Date {normalised!r} is in the future."
    if year == today.year and len(normalised) >= 7:
        month = int(normalised[5:7])
        if month > today.month:
            return f"Date {normalised!r} is in the future."
        if month == today.month and len(normalised) == 10:
            day = int(normalised[8:10])
            if day > today.day:
                return f"Date {normalised!r} is in the future."
    return None


def parse_dwc_date(
    raw: str,
    *,
    allow_interval: bool = False,
    no_future: bool = False,
) -> tuple[str, str | None]:
    """Parse *raw* and return ``(normalised_iso, error)``.

    *normalised_iso* — ISO 8601 string to store; empty string on failure.
    *error*          — None on success, human-readable message on failure.

    Accepted input:
      ISO 8601  : YYYY, YYYY-MM, YYYY-MM-DD  (zero-padding normalised)
      European  : DD.MM.YYYY, D.M.YYYY → YYYY-MM-DD
                  MM.YYYY              → YYYY-MM
      Interval  : <date>/<date>  (when allow_interval=True; eventDate only)

    no_future=True rejects dates that lie after today (dateIdentified fields).
    """
    raw = raw.strip()
    if not raw:
        return ("", None)

    if "/" in raw:
        if not allow_interval:
            return ("", "Date ranges are not allowed here — use a single date.")
        parts = raw.split("/", 1)
        left,  l_err = parse_dwc_date(parts[0].strip(), no_future=no_future)
        right, r_err = parse_dwc_date(parts[1].strip(), no_future=no_future)
        if l_err:
            return ("", f"Interval start: {l_err}")
        if r_err:
            return ("", f"Interval end: {r_err}")
        if left and right and left > right:
            return ("", f"Interval start ({left}) is after end ({right}).")
        return (f"{left}/{right}", None)

    if _ISO_YEAR.match(raw):
        if no_future and (err := _check_future(raw)):
            return ("", err)
        return (raw, None)

    if _ISO_YEAR_MONTH.match(raw):
        y, m = raw.split("-")
        if not (1 <= int(m) <= 12):
            return ("", f"Month {m} out of range (1–12).")
        normalised = f"{y}-{int(m):02d}"
        if no_future and (err := _check_future(normalised)):
            return ("", err)
        return (normalised, None)

    if _ISO_FULL.match(raw):
        p = raw.split("-")
        y, m, d = int(p[0]), int(p[1]), int(p[2])
        try:
            _date(y, m, d)
        except ValueError as exc:
            return ("", str(exc) + ".")
        normalised = f"{y:04d}-{m:02d}-{d:02d}"
        if no_future and (err := _check_future(normalised)):
            return ("", err)
        return (normalised, None)

    match = _EU_FULL.match(raw)
    if match:
        d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            _date(y, m, d)
        except ValueError as exc:
            return ("", str(exc) + ".")
        normalised = f"{y:04d}-{m:02d}-{d:02d}"
        if no_future and (err := _check_future(normalised)):
            return ("", err)
        return (normalised, None)

    match = _EU_MONTH_YEAR.match(raw)
    if match:
        m, y = int(match.group(1)), int(match.group(2))
        if not (1 <= m <= 12):
            return ("", f"Month {m} out of range (1–12).")
        normalised = f"{y:04d}-{m:02d}"
        if no_future and (err := _check_future(normalised)):
            return ("", err)
        return (normalised, None)

    return ("", f"{raw!r} is not a recognised date format. Use YYYY-MM-DD.")
