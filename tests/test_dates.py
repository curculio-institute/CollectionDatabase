"""Tests for app.services.dates.parse_dwc_date."""
import pytest
from datetime import date as _date
from app.services.dates import parse_dwc_date


def ok(raw, expected, **kw):
    norm, err = parse_dwc_date(raw, **kw)
    assert err is None, f"Expected ok for {raw!r}, got error: {err}"
    assert norm == expected, f"Expected {expected!r}, got {norm!r}"


def bad(raw, **kw):
    norm, err = parse_dwc_date(raw, **kw)
    assert err is not None, f"Expected error for {raw!r}, got {norm!r}"


# ── Empty ─────────────────────────────────────────────────────────────────────

def test_empty():
    ok("", "")
    ok("  ", "")


# ── ISO valid ─────────────────────────────────────────────────────────────────

def test_iso_year():           ok("2026",       "2026")
def test_iso_year_month():     ok("2026-06",    "2026-06")
def test_iso_full():           ok("2026-06-15", "2026-06-15")
def test_iso_pads_month():     ok("2026-6",     "2026-06")
def test_iso_pads_day():       ok("2026-6-5",   "2026-06-05")


# ── European conversion ───────────────────────────────────────────────────────

def test_eu_full():            ok("15.06.2026",  "2026-06-15")
def test_eu_full_short():      ok("5.6.2026",    "2026-06-05")
def test_eu_month_year():      ok("06.2026",     "2026-06")
def test_eu_month_year_short():ok("6.2026",      "2026-06")


# ── Intervals (allow_interval=True) ───────────────────────────────────────────

def test_interval_iso():
    ok("2026-06-15/2026-06-20", "2026-06-15/2026-06-20", allow_interval=True)

def test_interval_eu():
    ok("15.06.2026/20.06.2026", "2026-06-15/2026-06-20", allow_interval=True)

def test_interval_mixed():
    ok("15.06.2026/2026-06-20", "2026-06-15/2026-06-20", allow_interval=True)

def test_interval_year_only():
    ok("2025/2026", "2025/2026", allow_interval=True)

def test_interval_disallowed():
    bad("2026-06-15/2026-06-20")  # allow_interval defaults to False


# ── Invalid inputs ────────────────────────────────────────────────────────────

def test_bad_month():          bad("2026-13")
def test_bad_day():            bad("2026-02-30")
def test_bad_eu_day():         bad("31.02.2026")
def test_bad_format():         bad("sometime in June")   # was "June 2026" — now valid (#95)
def test_interval_reversed():  bad("2026-06-20/2026-06-15", allow_interval=True)


# ── Spelled-out months: English + German, full + abbreviations (#95) ──────────

def test_month_name_en_full():     ok("10 June 2026",     "2026-06-10")
def test_month_name_de_full():     ok("10. Juni 2026",    "2026-06-10")
def test_month_name_de_dots():     ok("10.Juni.2026",     "2026-06-10")
def test_month_name_month_year():  ok("Juni 2026",        "2026-06")
def test_month_name_umlaut():      ok("März 2020",        "2020-03")
def test_month_name_maerz():       ok("Maerz 2020",       "2020-03")
def test_month_name_abbrev():      ok("10. Sept. 2026",   "2026-09-10")   # Sept → 9, day kept
def test_month_name_mai_shared():  ok("Mai 2020",         "2020-05")
def test_month_name_case():        ok("10 JUNE 2026",     "2026-06-10")
def test_month_name_unknown():     bad("10. Foobar 2026")
def test_month_name_validates_day():bad("31. Juni 2026")  # June has 30 days


# ── Roman-numeral months (entomological label convention) ─────────────────────

def test_roman_full():             ok("10.IV.2020",   "2020-04-10")
def test_roman_lower():            ok("10.iv.2020",   "2020-04-10")
def test_roman_spaces():           ok("10. IV. 2020", "2020-04-10")
def test_roman_month_year():       ok("IV.2020",      "2020-04")
def test_roman_xii():              ok("10.XII.2020",  "2020-12-10")
def test_roman_i():                ok("I.2020",       "2020-01")
def test_roman_invalid_iiii():     bad("10.IIII.2020")
def test_roman_invalid_xx():       bad("10.XX.2020")


# ── Intervals with names / roman (allow_interval=True) ────────────────────────

def test_interval_month_names():
    ok("10. Juni 2026/15. Juni 2026", "2026-06-10/2026-06-15", allow_interval=True)

def test_interval_roman():
    ok("10.IV.2020/20.IV.2020", "2020-04-10/2020-04-20", allow_interval=True)


# ── Risk cases: refuse, never guess ───────────────────────────────────────────

def test_two_digit_year_refused():     bad("10.06.20")       # ambiguous century
def test_slash_day_month_refused():    bad("03/04/2020")     # DD/MM vs MM/DD unknowable
def test_eu_order_is_european():
    ok("06.05.2020", "2020-05-06")   # 6 May, deliberately NOT 5 June (DD.MM assumed)


# ── no_future constraint ──────────────────────────────────────────────────────

def test_no_future_past_ok():
    today = _date.today()
    past = _date(today.year - 1, today.month, today.day)
    ok(past.strftime("%Y-%m-%d"), past.strftime("%Y-%m-%d"), no_future=True)

def test_no_future_today_ok():
    today = _date.today()
    ok(today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), no_future=True)

def test_no_future_future_year():
    bad("2099", no_future=True)

def test_no_future_future_month():
    today = _date.today()
    future = _date(today.year, 12, 1) if today.month < 12 else _date(today.year + 1, 1, 1)
    bad(future.strftime("%Y-%m"), no_future=True)

def test_no_future_future_full():
    today = _date.today()
    import datetime
    future = today + datetime.timedelta(days=1)
    bad(future.strftime("%Y-%m-%d"), no_future=True)

def test_no_future_ignored_without_flag():
    ok("2099", "2099")  # no_future defaults to False


# ── Abbreviated ranges (the label convention: shared parts written once, on the right) ──
# Every case below is a real value from the collection spreadsheet.

def test_abbrev_day_range():
    ok("28.-30.08.2023", "2023-08-28/2023-08-30", allow_interval=True)

def test_abbrev_day_month_range():
    ok("19.5.-5.6.2019", "2019-05-19/2019-06-05", allow_interval=True)

def test_abbrev_padded_day_month_range():
    ok("15.07.-28.08.2007", "2007-07-15/2007-08-28", allow_interval=True)

def test_abbrev_roman_month_range():
    ok("16.V-23.9.2006", "2006-05-16/2006-09-23", allow_interval=True)

def test_abbrev_month_range():
    ok("9.-10.2019", "2019-09/2019-10", allow_interval=True)

def test_abbrev_month_range_padded():
    ok("05-06.2022", "2022-05/2022-06", allow_interval=True)

def test_abbrev_year_range():
    ok("2015-2016", "2015/2016", allow_interval=True)

def test_abbrev_range_needs_allow_interval():
    bad("28.-30.08.2023")

def test_abbrev_range_reversed_is_refused():
    # 22.06. cannot precede 13.03. of the same year — a data error, not a reading to guess.
    bad("22.06.-13.03.2006", allow_interval=True)

def test_abbrev_range_impossible_day_is_refused():
    bad("35.8.2015", allow_interval=True)

def test_abbrev_range_out_of_range_month_is_refused():
    bad("27-06.2016", allow_interval=True)

def test_iso_full_is_not_read_as_a_range():
    # The hyphens of an ISO date must never be taken for a range separator.
    ok("2026-06-15", "2026-06-15", allow_interval=True)

def test_iso_year_month_is_not_read_as_a_range():
    ok("2026-06", "2026-06", allow_interval=True)

def test_unparseable_stays_unparseable():
    for raw in ("<2021", "ca. 2006?", "?", "1.-15-vi.2001"):
        bad(raw, allow_interval=True)
