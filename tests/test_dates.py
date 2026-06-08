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
def test_bad_format():         bad("June 2026")
def test_interval_reversed():  bad("2026-06-20/2026-06-15", allow_interval=True)


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
