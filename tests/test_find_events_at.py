"""events.find_events_at — the Digitize check panel's "an event already exists here"
suggestion. Matches on coordinates + date ONLY; every other field is ignored."""
import app.services.events as ev_svc


def _ev(session, **kw):
    return ev_svc.create_collecting_event(session, **kw)


def test_matches_identical_coordinates_and_date(session):
    e = _ev(session, decimal_latitude="48.137", decimal_longitude="11.575",
            event_date="2024-06-15", locality="Isarauen")
    hits = ev_svc.find_events_at(session, 48.137, 11.575, "2024-06-15")
    assert [h["id"] for h in hits] == [e.id]
    assert hits[0]["n_specimens"] == 0
    assert "Isarauen" in hits[0]["label"]


def test_other_fields_are_not_taken_into_account(session):
    a = _ev(session, decimal_latitude="48.137", decimal_longitude="11.575",
            event_date="2024-06-15", locality="A", municipality="München")
    b = _ev(session, decimal_latitude="48.137", decimal_longitude="11.575",
            event_date="2024-06-15", locality="B", field_number="X-1")
    hits = ev_svc.find_events_at(session, 48.137, 11.575, "2024-06-15")
    assert {h["id"] for h in hits} == {a.id, b.id}


def test_different_date_or_coordinate_does_not_match(session):
    _ev(session, decimal_latitude="48.137", decimal_longitude="11.575",
        event_date="2024-06-15")
    assert ev_svc.find_events_at(session, 48.137, 11.575, "2024-06-16") == []
    assert ev_svc.find_events_at(session, 48.1371, 11.575, "2024-06-15") == []
    assert ev_svc.find_events_at(session, 48.137, 11.5751, "2024-06-15") == []


def test_an_incomplete_key_matches_nothing(session):
    # A dateless or coordinate-less event must never be suggested for a dateless or
    # coordinate-less form: "both blank" is not "identical".
    _ev(session, decimal_latitude="48.137", decimal_longitude="11.575")
    _ev(session, event_date="2024-06-15", locality="no coords")
    assert ev_svc.find_events_at(session, 48.137, 11.575, None) == []
    assert ev_svc.find_events_at(session, 48.137, 11.575, "  ") == []
    assert ev_svc.find_events_at(session, None, None, "2024-06-15") == []
    assert ev_svc.find_events_at(session, 48.137, None, "2024-06-15") == []
