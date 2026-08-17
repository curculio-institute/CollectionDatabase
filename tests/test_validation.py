"""Tests for app.services.validation."""
from app.services.validation import validate_event_fields, missing_event_fields


# ── validate_event_fields (hard DB-constraint checks) ──────────────────────────

def test_validate_event_fields_empty_ok():
    assert validate_event_fields({}) is None


def test_validate_event_fields_bad_country_code():
    assert validate_event_fields({"country_iso": "DEU"}) is not None


def test_validate_event_fields_lat_out_of_range():
    assert validate_event_fields({"decimal_latitude": "95"}) is not None


def test_validate_event_fields_lon_out_of_range():
    assert validate_event_fields({"decimal_longitude": "-200"}) is not None


def test_validate_event_fields_negative_uncertainty():
    assert validate_event_fields({"coordinate_uncertainty_in_meters": "-5"}) is not None


def test_validate_event_fields_all_valid():
    fields = {
        "country_iso": "DE", "decimal_latitude": "48.1", "decimal_longitude": "11.5",
        "coordinate_uncertainty_in_meters": "30",
    }
    assert validate_event_fields(fields) is None


# ── missing_event_fields (soft completeness check, #173) ───────────────────────

_COMPLETE = {
    "decimal_latitude": "48.1", "decimal_longitude": "11.5",
    "municipality": "Augsburg", "event_date": "2026-06-15",
}


def test_missing_event_fields_all_present():
    assert missing_event_fields(_COMPLETE, recorded_by=True) == []


def test_missing_event_fields_empty_dict_reports_everything():
    missing = missing_event_fields({}, recorded_by=False)
    assert missing == ["Coordinates", "Municipality", "Date", "Recorded by"]


def test_missing_event_fields_lone_latitude_is_missing_coordinates():
    fields = {**_COMPLETE, "decimal_longitude": ""}
    assert "Coordinates" in missing_event_fields(fields, recorded_by=True)


def test_missing_event_fields_lone_longitude_is_missing_coordinates():
    fields = {**_COMPLETE, "decimal_latitude": None}
    assert "Coordinates" in missing_event_fields(fields, recorded_by=True)


def test_missing_event_fields_blank_municipality():
    fields = {**_COMPLETE, "municipality": "   "}
    assert "Municipality" in missing_event_fields(fields, recorded_by=True)


def test_missing_event_fields_blank_date():
    fields = {**_COMPLETE, "event_date": ""}
    assert "Date" in missing_event_fields(fields, recorded_by=True)


def test_missing_event_fields_no_recorded_by():
    assert missing_event_fields(_COMPLETE, recorded_by=False) == ["Recorded by"]
