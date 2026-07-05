"""DwC CSV header normalisation + row mapping.

The importer expects valid Darwin Core: headers are DwC terms, normalised only
for casing/separators. It does NOT accept informal synonyms or remap one DwC
term onto a different column.
"""
from app.services.dwc_import import (
    _ALIASES, _norm_key, parse_csv, row_to_event_fields, row_to_specimen_prefill,
)


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
                "country", "state_province", "administrative_region", "county", "island"}
    unknown = set(row_to_event_fields({})) - cols - resolved
    assert not unknown, f"unresolved/unknown collecting_event keys: {sorted(unknown)}"
