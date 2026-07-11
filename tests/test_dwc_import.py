"""DwC CSV header normalisation + row mapping.

The importer expects valid Darwin Core: headers are DwC terms, normalised only
for casing/separators. It does NOT accept informal synonyms or remap one DwC
term onto a different column.
"""
import pytest

from app.services.dwc_import import _HOST_QUALIFIER_CANONICAL  # noqa: F401
from app.services.dwc_import import (
    _ALIASES, _norm_key, parse_csv, parse_individual_count,
    host_qualifier, host_search_query, row_host_name, row_to_event_fields,
    row_to_specimen_prefill,
)


class TestHostAssociation:
    """associatedOrganisms → host biological association (#6)."""

    def test_associated_organisms_is_a_recognised_term(self):
        # A real DwC term, normalised only for casing — reaches the row dict.
        rows = parse_csv("scientificName,associatedOrganisms\nSitona sp.,Salix\n")
        assert row_host_name(rows[0]) == "Salix"

    def test_associated_taxa_is_not_aliased_onto_it(self):
        # associatedTaxa is a distinct DwC term — deliberately NOT conflated.
        assert _norm_key("associatedTaxa") not in _ALIASES

    def test_row_host_name_blank_when_absent(self):
        assert row_host_name({"scientificName": "Sitona lineatus"}) == ""
        assert row_host_name({"associatedOrganisms": "   "}) == ""

    def test_host_search_query_strips_open_nomenclature(self):
        assert host_search_query("Betula sp.") == "Betula"
        assert host_search_query("Silene cf. otites") == "Silene otites"
        assert host_search_query("Quercus spp.") == "Quercus"
        assert host_search_query("Salix") == "Salix"          # bare genus unchanged
        assert host_search_query("Rumex acetosella") == "Rumex acetosella"

    # The qualifier that host_search_query() strips is not noise — "Betula sp." asserts the
    # species is undetermined. Dropping it recorded the host as a flat determination of
    # *Betula*, a claim the row never made. host_qualifier() recovers it (§2).

    def test_host_qualifier_recovers_what_the_query_strips(self):
        assert host_qualifier("Betula sp.") == "sp."
        assert host_qualifier("Silene cf. otites") == "cf."
        assert host_qualifier("Quercus spp.") == "spp."
        assert host_qualifier("Rubus agg.") == "agg."
        assert host_qualifier("Salix aff. caprea") == "aff."

    def test_host_qualifier_is_none_for_a_plain_name(self):
        assert host_qualifier("Salix") is None
        assert host_qualifier("Rumex acetosella") is None
        assert host_qualifier("") is None

    def test_host_qualifier_canonicalises_a_dotless_token(self):
        # Real data is inconsistent; the DB CHECK only accepts the dotted forms.
        assert host_qualifier("Betula sp") == "sp."
        assert host_qualifier("Silene cf otites") == "cf."

    def test_host_qualifier_is_always_a_storable_value(self):
        # Every value it can return must satisfy ck_td_identification_qualifier —
        # otherwise a valid CSV would blow up at save time with an IntegrityError.
        from app.vocab import IDENTIFICATION_QUALIFIERS
        from app.services.dwc_import import _HOST_QUALIFIER_CANONICAL
        assert set(_HOST_QUALIFIER_CANONICAL.values()) <= set(IDENTIFICATION_QUALIFIERS)

    def test_query_and_qualifier_split_the_name_between_them(self):
        # Nothing in the host cell is lost: it is either part of the name or the qualifier.
        for raw in ("Betula sp.", "Silene cf. otites", "Salix", "Quercus spp."):
            kept = host_search_query(raw).split()
            qual = host_qualifier(raw)
            n_qual_tokens = sum(1 for t in raw.split()
                                if t.lower() in _HOST_QUALIFIER_CANONICAL)
            assert len(kept) + n_qual_tokens == len(raw.split())
            assert (qual is not None) == (n_qual_tokens > 0)


class TestParseIndividualCount:
    """individualCount is parsed defensively (#4): the standard value is 1, a
    deliberate 0 is preserved, and a non-numeric cell must not raise."""

    def test_empty_and_none_default_to_one(self):
        assert parse_individual_count(None) == (1, None)
        assert parse_individual_count("") == (1, None)
        assert parse_individual_count("   ") == (1, None)

    def test_valid_positive_integer_kept(self):
        assert parse_individual_count("3") == (3, None)
        assert parse_individual_count(" 12 ") == (12, None)
        assert parse_individual_count(5) == (5, None)

    def test_deliberate_zero_is_preserved(self):
        # The DB CHECK allows >= 0, so an explicit 0 is a real value, not empty.
        assert parse_individual_count("0") == (0, None)
        assert parse_individual_count(0) == (0, None)

    def test_non_numeric_defaults_to_one_with_warning(self):
        count, warn = parse_individual_count("F")
        assert count == 1
        assert warn is not None and "F" in warn

    def test_negative_defaults_to_one_with_warning(self):
        count, warn = parse_individual_count("-2")
        assert count == 1
        assert warn is not None


def test_surplus_columns_row_refuses_loudly():
    """A row with more values than headers (usually an unescaped comma) shifts
    every field after it, so the row is untrustworthy — parse_csv refuses the
    whole import with a clear, row-identifying error rather than crashing in
    _norm_key(None) (#68) or silently keeping shifted data (#62)."""
    csv_text = (
        "scientificName,locality\n"
        "Sitona lineatus,near river\n"                 # good row
        "Otiorhynchus norici,Berchtesgaden, Bavaria\n"  # unescaped comma → surplus
    )
    with pytest.raises(ValueError) as exc:
        parse_csv(csv_text)
    msg = str(exc.value)
    assert "more values than there are columns" in msg
    assert "Bavaria" in msg          # names the surplus value
    assert "line 3" in msg           # names the offending row


def test_trailing_empty_delimiters_are_tolerated():
    """Trailing commas produce empty surplus values — harmless, not a misaligned
    row — so they are dropped and the import proceeds."""
    rows = parse_csv("scientificName,locality\nSitona lineatus,near river,,\n")
    assert len(rows) == 1
    assert rows[0]["scientificName"] == "Sitona lineatus"
    assert rows[0]["locality"] == "near river"


def test_alias_keys_are_all_normalised_no_dead_entries():
    # Every alias key must equal its own _norm_key, else the lookup (which runs
    # _norm_key first) can never reach it. Guards against re-adding snake_case
    # variants that silently go dead.
    for k in _ALIASES:
        assert k == _norm_key(k), f"{k!r} is unreachable (normalises to {_norm_key(k)!r})"


def test_island_normalises_and_flows_into_event_fields():
    rows = parse_csv("scientificName,Island\nOtiorhynchus norici,Sardinia\n")
    assert rows[0]["island"] == "Sardinia"
    assert row_to_event_fields(rows[0])["island"] == "Sardinia"


def test_casing_and_separators_normalise_to_canonical_term():
    # Valid DwC term in any casing / with separators resolves to canonical camelCase.
    rows = parse_csv("Scientific Name,DECIMALLATITUDE,verbatim_locality\nSitona lineatus,42.5,near river\n")
    r = rows[0]
    assert r["scientificName"] == "Sitona lineatus"
    assert r["decimalLatitude"] == "42.5"
    assert r["verbatimLocality"] == "near river"


def test_informal_synonyms_are_not_mapped():
    # "leg"/"lat"/"species" are not Darwin Core terms — they pass through as-is,
    # not silently mapped onto recordedBy/decimalLatitude/scientificName.
    rows = parse_csv("species,leg,lat\nSitona lineatus,Smith,42.5\n")
    r = rows[0]
    assert "scientificName" not in r
    assert "recordedBy" not in r
    assert "decimalLatitude" not in r
    assert row_to_event_fields(r)["recorded_by"] == ""


def test_no_cross_column_remap_for_occurrence_remarks():
    # occurrenceRemarks must NOT feed the materialEntityRemarks column; only the
    # column's own DwC term does.
    rows = parse_csv("scientificName,occurrenceRemarks\nSitona lineatus,on nettle\n")
    assert row_to_specimen_prefill(rows[0])["occurrence_remarks"] == ""
    rows = parse_csv("scientificName,materialEntityRemarks\nSitona lineatus,on nettle\n")
    assert row_to_specimen_prefill(rows[0])["occurrence_remarks"] == "on nettle"


def test_event_field_keys_are_all_resolvable_or_columns():
    """#61 drift-guard: every key row_to_event_fields emits must be either a real
    CollectingEvent column or one the import resolves before saving
    (recorded_by/habitat/sampling + geo names). Otherwise the create_collecting_event
    unknown-key guard would reject a legitimate import (or silently drop the field)."""
    from sqlalchemy import inspect
    from app.models import CollectingEvent
    cols = {c.key for c in inspect(CollectingEvent).mapper.column_attrs}
    resolved = {"recorded_by", "habitat", "sampling_protocol",
                "country", "state_province", "administrative_region", "county", "island",
                # not columns: they identify WHICH vocab row the name means (0056/0057)
                "country_iso", "state_province_iso"}
    unknown = set(row_to_event_fields({})) - cols - resolved
    assert not unknown, f"unresolved/unknown collecting_event keys: {sorted(unknown)}"
