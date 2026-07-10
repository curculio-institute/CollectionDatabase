"""normalise_row_dates: import path parses + preserves dates, refuses bad ones (#1)."""
import pytest
from app.services.dwc_import import normalise_row_dates


def test_reformats_and_preserves_raw():
    ovr, err = normalise_row_dates({"eventDate": "15.07.2005"})
    assert err is None
    assert ovr["event_date"] == "2005-07-15"
    assert ovr["verbatim_event_date"] == "15.07.2005"   # raw kept for audit


def test_month_name_and_roman():
    assert normalise_row_dates({"eventDate": "10. Juni 2005"})[0]["event_date"] == "2005-06-10"
    assert normalise_row_dates({"eventDate": "10.IV.2005"})[0]["event_date"] == "2005-04-10"


def test_iso_passthrough_no_verbatim():
    ovr, err = normalise_row_dates({"eventDate": "2005-07-15"})
    assert err is None and ovr["event_date"] == "2005-07-15"
    assert "verbatim_event_date" not in ovr        # unchanged → nothing to preserve


def test_existing_verbatim_not_clobbered():
    ovr, _ = normalise_row_dates({"eventDate": "15.07.2005", "verbatimEventDate": "mid-July"})
    assert "verbatim_event_date" not in ovr         # row already supplied one


def test_interval():
    ovr, err = normalise_row_dates({"eventDate": "15.07.2005/20.07.2005"})
    assert err is None and ovr["event_date"] == "2005-07-15/2005-07-20"


def test_bad_event_date_refused():
    ovr, err = normalise_row_dates({"eventDate": "15.07.05"})   # 2-digit year
    assert ovr == {} and "eventDate" in err


def test_bad_dateidentified_refused():
    ovr, err = normalise_row_dates({"eventDate": "2005", "dateIdentified": "garbage"})
    assert ovr == {} and "dateIdentified" in err


def test_empty_dates_ok():
    ovr, err = normalise_row_dates({})
    assert err is None and ovr["event_date"] == "" and ovr["date_identified"] == ""


# ── integration: the real save path stores ISO + preserves the raw ──────────────
import app.services.dwc_import as dwc_svc
import app.services.events as ev_svc
from app.models import CollectingEvent


def test_saved_event_has_iso_date_and_verbatim(session):
    """Mirror _on_assign's date handling against the real events service (#1)."""
    row = {"eventDate": "15.07.2005", "locality": "Staffelsee"}
    event_fields = dwc_svc.row_to_event_fields(row)
    # habitat / samplingProtocol / recordedBy resolution omitted (not under test)
    for k in ("habitat", "sampling_protocol", "recorded_by", "country_iso"):
        event_fields.pop(k, None)

    ovr, err = dwc_svc.normalise_row_dates(row)
    assert err is None
    event_fields["event_date"] = ovr["event_date"]
    if "verbatim_event_date" in ovr:
        event_fields["verbatim_event_date"] = ovr["verbatim_event_date"]

    ev = ev_svc.create_collecting_event(session, **event_fields)
    session.flush()
    got = session.get(CollectingEvent, ev.id)
    assert got.event_date == "2005-07-15"            # ISO stored, not "15.07.2005"
    assert got.verbatim_event_date == "15.07.2005"   # raw preserved
