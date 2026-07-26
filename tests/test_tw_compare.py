"""The TaxonWorks compare lookup must never trust that its filter applied (#149 Step 1).

CLAUDE.md §5c: TaxonWorks silently ignores an unknown query param and answers with the
unfiltered table, so a result set is never self-evidently filtered. The dangerous reading
of a broken filter is *not* "these are matches" but "this specimen is not on TaxonWorks":
the export excludes what TW already holds, so a lookup that wrongly finds nothing
re-uploads specimens into a CREATE-ONLY importer and mints duplicates the API cannot
delete. Hence a suspect response raises rather than being quietly filtered down.
"""
import pytest

from app.services.tw_compare import _verify_filter_applied
from app.services.taxonworks import TaxonWorksUnreachable


def test_clean_response_passes_through():
    rows = [{"catalogNumber": "JJPC-00001", "dwc_occurrence_object_id": 7}]
    assert _verify_filter_applied(rows, "JJPC-00001") == rows


def test_empty_response_is_a_real_not_on_taxonworks():
    """No rows is a legitimate answer — the specimen simply is not there."""
    assert _verify_filter_applied([], "JJPC-00001") == []


def test_several_rows_for_the_same_number_are_kept():
    """A genuine duplicate on TW (#149 step 1.3) must survive the check."""
    rows = [
        {"catalogNumber": "JJPC-00001", "dwc_occurrence_object_id": 7},
        {"catalogNumber": "JJPC-00001", "dwc_occurrence_object_id": 9},
    ]
    assert _verify_filter_applied(rows, "JJPC-00001") == rows


def test_a_foreign_catalog_number_is_a_lookup_failure():
    """The unfiltered-table symptom: rows for other specimens came back."""
    rows = [
        {"catalogNumber": "JJPC-00001"},
        {"catalogNumber": "JJPC-00002"},
    ]
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        _verify_filter_applied(rows, "JJPC-00001")


def test_rows_without_a_catalog_number_are_a_lookup_failure():
    """dwc_occurrences rows are sparse — a row with no catalogNumber key cannot have
    matched an applied exact filter, so its presence means the filter was ignored."""
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        _verify_filter_applied([{"dwc_occurrence_object_id": 7}], "JJPC-00001")


@pytest.mark.parametrize("payload", [{"error": "unauthorized"}, None, "nope", [None]])
def test_a_non_list_payload_is_a_lookup_failure(payload):
    with pytest.raises(TaxonWorksUnreachable, match="lookup failure"):
        _verify_filter_applied(payload, "JJPC-00001")
