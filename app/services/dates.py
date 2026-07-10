"""Date parsing and normalisation for Darwin Core date fields.

Accepted, all normalised to ISO 8601 (YYYY, YYYY-MM, or YYYY-MM-DD):
    ISO         : YYYY, YYYY-MM, YYYY-MM-DD
    European    : DD.MM.YYYY, MM.YYYY            (dot-separated; NOT US MM/DD)
    Spelled     : "10. Juni 2026", "June 2026"  (English + German month names, #95)
    Roman month : "10.IV.2020", "IV.2020"       (I–XII, the entomological convention)

Intervals (ISO 8601 `<date>/<date>`) are accepted when allow_interval=True — eventDate,
dateIdentified, and life-stage dates.

Deliberately refused, never guessed (a loud error, so the caller can reject the row —
CLAUDE.md §2): 2-digit years (`10.06.20` — ambiguous century), slash-separated day/month
(`03/04/2020` — DD/MM vs MM/DD is unknowable), impossible dates. Day/month order is assumed
**European DD.MM** to match the data; the importer preserves the raw string in
verbatimEventDate so a misread is always auditable.
"""
from __future__ import annotations

import re
from datetime import date as _date, datetime as _datetime

_ISO_YEAR       = re.compile(r'^\d{4}$')
_ISO_YEAR_MONTH = re.compile(r'^\d{4}-\d{1,2}$')
_ISO_FULL       = re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$')
_EU_FULL        = re.compile(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$')   # DD.MM.YYYY
_EU_MONTH_YEAR  = re.compile(r'^(\d{1,2})\.(\d{4})$')              # MM.YYYY
_SPACE_FULL     = re.compile(r'^(\d{4})\s+(\d{1,2})\s+(\d{1,2})$') # YYYY MM DD

# Month names, English + German, full and common abbreviations (#95). Values are the month
# number. German full forms for months whose name differs from English; the shared ones
# (April, August, September, November) are covered by the English entries.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
    "oktober": 10, "dezember": 12,
    "mär": 3, "okt": 10, "dez": 12,
    # Roman-numeral months, the entomological label convention: "10.IV.2020" = 10 Apr 2020.
    # Matched case-insensitively; no roman value collides with a word abbreviation above.
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
}

# `[DD sep] MonthName sep YYYY` — dots and/or spaces as separators: "10.Juni.2026",
# "10. Juni 2026", "Juni 2026", "10 June 2026", "June 2026". A purely numeric string never
# matches (the month group requires letters), so existing formats are untouched.
_MONTH_NAME_DATE = re.compile(
    r'^(?:(\d{1,2})[.\s]+)?([A-Za-zäöüÄÖÜ]+)\.?[.\s]+(\d{4})$'
)


def _normalise_month_name(raw: str) -> str:
    """Rewrite a spelled-out month into the numeric form the parser already handles.

    "10. Juni 2026" -> "10.6.2026" (then DD.MM.YYYY), "Juni 2026" -> "6.2026" (then MM.YYYY).
    Returns *raw* unchanged when there is no month name or the word is not a known month, so
    the caller's existing branches (and their error messages) still apply.
    """
    m = _MONTH_NAME_DATE.match(raw)
    if not m:
        return raw
    day, word, year = m.group(1), m.group(2).lower(), m.group(3)
    num = _MONTHS.get(word)
    if num is None:
        return raw
    return f"{day}.{num}.{year}" if day else f"{num}.{year}"


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
      Space-sep : YYYY MM DD             → YYYY-MM-DD
      European  : DD.MM.YYYY, D.M.YYYY  → YYYY-MM-DD
                  MM.YYYY               → YYYY-MM
      Interval  : <date>/<date>  (when allow_interval=True; eventDate only)

    no_future=True rejects dates that lie after today (dateIdentified fields).
    """
    raw = raw.strip()
    if not raw:
        return ("", None)

    # Normalize YYYY MM DD (space-separated) to YYYY-MM-DD before further checks.
    m = _SPACE_FULL.match(raw)
    if m:
        raw = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Spelled-out months (#95): rewrite to the numeric form handled below. A no-op on a
    # numeric string, and on an interval "a/b" (each side is rewritten in the recursion).
    raw = _normalise_month_name(raw)

    if "/" in raw:
        if not allow_interval:
            return ("", "Date ranges are not allowed here — use a single date.")
        parts = raw.split("/", 1)
        if not parts[0].strip() or not parts[1].strip():
            # An open-ended range (one side blank) is not a valid ISO 8601
            # interval — refuse it rather than storing a malformed "start/" (#69).
            return ("", "A date range needs both a start and an end "
                        "(e.g. 2024-06-15/2024-06-20).")
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
