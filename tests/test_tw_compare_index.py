"""The catalog-number identity index: `cached` is the join key, not `identifier`.

Rows below are the shape **measured** against sandbox.taxonworks.org (2026-07-26). The
discovery worth guarding: TaxonWorks splits the catalog number across the namespace and
the identifier — our local `JJPC-00001` is stored as `identifier="00001"` under namespace
`JJPC` (delimiter `-`), and only `cached` renders back to `JJPC-00001`. Joining on
`identifier` scored 0 overlap on all 39 specimens.
"""
from app.services.tw_compare import _namespace_short_name, build_catalog_index


def _row(identifier="00001", cached="JJPC-00001", obj_id=1526217, ns_id=6057,
         namespace=True, **over):
    row = {
        "id": 1,
        "identifier": identifier,
        "cached": cached,
        "type": "Identifier::Local::CatalogNumber",
        "identifier_object_type": "CollectionObject",
        "identifier_object_id": obj_id,
        "namespace_id": ns_id,
    }
    if namespace:
        row["namespace"] = {
            "id": ns_id, "name": "Jakob Jilg Personal Collection",
            "short_name": "JJPC", "delimiter": "-",
        }
    row.update(over)
    return row


def test_the_join_key_is_cached_not_identifier():
    index = build_catalog_index([_row()])
    assert index.has("JJPC-00001")
    assert not index.has("00001")
    assert index.get("JJPC-00001")[0].identifier == "00001"
    assert index.get("JJPC-00001")[0].tw_object_id == 1526217


def test_namespace_short_name_comes_from_the_extended_object():
    assert _namespace_short_name(_row()) == "JJPC"


def test_namespace_short_name_falls_back_to_stripping_cached():
    """`extend[]=namespace` was measured to work here, but a silently dropped param must
    degrade rather than lie: cached is short_name + delimiter + identifier."""
    assert _namespace_short_name(_row(namespace=False)) == "JJPC"


def test_non_collection_object_rows_are_dropped():
    """Of 181 CatalogNumbers on the sandbox project, 140 hang on Containers and 2 on
    Images — filtered client-side so a server filter that stops applying only costs
    bandwidth."""
    rows = [
        _row(),
        _row(cached="JJPC-00002", identifier_object_type="Container", obj_id=2),
        _row(cached="JJPC-00003", identifier_object_type="Image", obj_id=3),
    ]
    index = build_catalog_index(rows)
    assert [e.catalog_number for e in index.entries] == ["JJPC-00001"]


def test_other_identifier_types_are_dropped():
    rows = [_row(), _row(cached="X-1", type="Identifier::Local::Import::Dwc", obj_id=9)]
    index = build_catalog_index(rows)
    assert [e.catalog_number for e in index.entries] == ["JJPC-00001"]


def test_rows_without_a_cached_form_or_object_id_are_dropped():
    """No cached form means no join key; the row cannot be matched to anything."""
    rows = [_row(cached=""), _row(cached="JJPC-00004", obj_id=None)]
    assert build_catalog_index(rows).entries == ()


def test_the_same_catalog_number_twice_is_kept_as_two_entries():
    """A duplicate on TaxonWorks (#149 step 1.3) must survive as both records."""
    rows = [_row(obj_id=11), _row(obj_id=22)]
    index = build_catalog_index(rows)
    assert len(index.get("JJPC-00001")) == 2
    assert {e.tw_object_id for e in index.get("JJPC-00001")} == {11, 22}


def test_entries_from_a_foreign_namespace_are_indexed_too():
    """Existence must be namespace-UNSCOPED: a specimen filed under someone else's
    namespace is still on TaxonWorks, and calling it absent would re-upload it."""
    rows = [_row(), _row(cached="SMITH-00009", identifier="00009", ns_id=99,
                         namespace=False, obj_id=77)]
    index = build_catalog_index(rows)
    assert index.has("SMITH-00009")
    assert index.get("SMITH-00009")[0].namespace_short_name == "SMITH"
