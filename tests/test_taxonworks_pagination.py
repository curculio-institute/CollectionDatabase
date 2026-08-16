"""PaginationTracker (#167) — the pagination-total bookkeeping shared by
tw_compare.fetch_catalog_index and tw_media_compare's fetch functions. Pure, no
network: httpx.Response is used only as a headers container, never sent anywhere.
"""
import httpx

from app.services.taxonworks import PaginationTracker


def _response(headers: dict | None = None) -> httpx.Response:
    return httpx.Response(200, headers=headers or {})


def test_reads_total_once_from_first_page_only():
    tracker = PaginationTracker()
    tracker.record_page(_response({"pagination-total": "5"}), [1, 2, 3])
    # A later page carrying a different value must not override the first reading.
    tracker.record_page(_response({"pagination-total": "999"}), [4, 5])
    assert tracker.total == 5
    assert tracker.received == 5


def test_falls_back_to_x_total_header():
    tracker = PaginationTracker()
    tracker.record_page(_response({"x-total": "2"}), [1, 2])
    assert tracker.total == 2


def test_no_total_header_leaves_total_none():
    tracker = PaginationTracker()
    tracker.record_page(_response(), [1, 2, 3])
    assert tracker.total is None
    assert tracker.received == 3


def test_is_complete_on_empty_page():
    tracker = PaginationTracker()
    assert tracker.is_complete([], per_page=500) is True


def test_is_complete_once_total_reached():
    tracker = PaginationTracker()
    tracker.record_page(_response({"pagination-total": "3"}), [1, 2, 3])
    assert tracker.is_complete([1, 2, 3], per_page=500) is True


def test_is_complete_on_short_page_without_a_total():
    tracker = PaginationTracker()
    tracker.record_page(_response(), [1, 2])
    assert tracker.is_complete([1, 2], per_page=500) is True


def test_not_complete_on_a_full_page_with_total_not_yet_reached():
    tracker = PaginationTracker()
    tracker.record_page(_response({"pagination-total": "10"}), [1] * 5)
    assert tracker.is_complete([1] * 5, per_page=5) is False


def test_check_complete_raises_on_a_short_read():
    """The dangerous direction (CLAUDE.md §5c): a short read must never be silently
    trusted as "that's everything", since it reads as "not on TaxonWorks" downstream."""
    tracker = PaginationTracker()
    tracker.record_page(_response({"pagination-total": "10"}), [1, 2, 3])
    try:
        tracker.check_complete(what="the widgets endpoint")
        assert False, "expected TaxonWorksUnreachable"
    except Exception as exc:
        assert "3 of 10" in str(exc)


def test_check_complete_passes_when_total_reached():
    tracker = PaginationTracker()
    tracker.record_page(_response({"pagination-total": "3"}), [1, 2, 3])
    tracker.check_complete(what="the widgets endpoint")   # must not raise


def test_check_complete_passes_when_no_total_was_ever_reported():
    tracker = PaginationTracker()
    tracker.record_page(_response(), [1, 2])
    tracker.check_complete(what="the widgets endpoint")   # must not raise
