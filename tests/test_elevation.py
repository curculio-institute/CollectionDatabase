"""Elevation: a single value is an exact elevation (decided 2026-10-20) — mirrored into
the empty bound on every event write; labels print it once, without a trailing '.0'."""
import pytest


class TestElevation:
    """One elevation given = an exact elevation: mirrored into the empty bound on every
    event write; labels print it once, without a trailing '.0'."""

    def test_create_mirrors_a_lone_min_or_max(self, session):
        from app.services.events import create_collecting_event
        a = create_collecting_event(session, locality="A", minimum_elevation_in_meters="1650")
        b = create_collecting_event(session, locality="B", maximum_elevation_in_meters=800)
        assert (a.minimum_elevation_in_meters, a.maximum_elevation_in_meters) == (1650, 1650)
        assert (b.minimum_elevation_in_meters, b.maximum_elevation_in_meters) == (800, 800)

    def test_a_range_and_no_elevation_are_left_alone(self, session):
        from app.services.events import create_collecting_event
        r = create_collecting_event(session, locality="R", minimum_elevation_in_meters=1200,
                                    maximum_elevation_in_meters=1350)
        n = create_collecting_event(session, locality="N")
        assert (r.minimum_elevation_in_meters, r.maximum_elevation_in_meters) == (1200, 1350)
        assert (n.minimum_elevation_in_meters, n.maximum_elevation_in_meters) == (None, None)

    def test_update_mirrors_too(self, session):
        from app.services.events import create_collecting_event, update_collecting_event
        ev = create_collecting_event(session, locality="U")
        update_collecting_event(session, ev.id, minimum_elevation_in_meters="",
                                maximum_elevation_in_meters="2100")
        assert (ev.minimum_elevation_in_meters, ev.maximum_elevation_in_meters) == (2100, 2100)

    def test_exact_match_reuse_sees_the_mirrored_value(self, session):
        from app.services.events import get_or_create_exact_event
        first, created = get_or_create_exact_event(session, locality="M",
                                                   minimum_elevation_in_meters=999)
        again, created2 = get_or_create_exact_event(session, locality="M",
                                                    minimum_elevation_in_meters=999)
        assert created and not created2 and again.id == first.id

    @pytest.mark.parametrize("lo,hi,want", [
        (1650.0, 1650.0, "1650 m"), (1200.0, 1350.0, "1200–1350 m"),
        (1200.5, None, "1200.5 m"), (None, 800.0, "800 m"), (0.0, 0.0, "0 m"),
        (None, None, ""),
    ])
    def test_label_format(self, lo, hi, want):
        from app.services.labels import format_elevation
        assert format_elevation(lo, hi) == want
