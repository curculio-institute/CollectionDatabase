"""Biological-association compare (spec 2026-10-17): TaxonWorks fetches re-verified,
the host-name rule, and the pure comparison."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.tw_association_compare as cmp
from app.config import AppConfig
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_association_compare import TwName


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="t"))


def _serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cmp, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


# ── host_verdict ────────────────────────────────────────────────────────────────

def test_host_verdict_exact_and_whitespace():
    assert cmp.host_verdict("Typha", "L.", TwName(1, "Typha", "L.", "genus")) == ("match", ())
    assert cmp.host_verdict("Typha  latifolia", "L.",
                            TwName(1, "Typha latifolia", "L.", "species"))[0] == "match"


def test_host_verdict_subgenus_only_difference():
    verdict, notes = cmp.host_verdict("Carabus (Eucarabus) arvensis", "Herbst, 1784",
                                      TwName(1, "Carabus arvensis", "Herbst, 1784", "species"))
    assert verdict == "subgenus"
    assert any("subgenus" in n for n in notes)


def test_host_verdict_authorship_differs_is_a_difference():
    verdict, notes = cmp.host_verdict("Typha", "L.", TwName(1, "Typha", "Mill.", "genus"))
    assert verdict == "differs"
    assert "authorship" in notes[0]


def test_host_verdict_missing_authorship_matches_with_note():
    verdict, notes = cmp.host_verdict("Typha", None, TwName(1, "Typha", "L.", "genus"))
    assert verdict == "match"
    assert any("authorship not compared" in n for n in notes)


def test_host_verdict_transliteration_matches_with_note():
    verdict, notes = cmp.host_verdict("Otiorhynchini", "Schönherr, 1826",
                                      TwName(1, "Otiorhynchini", "Schoenherr, 1826", "tribe"))
    assert verdict == "match"
    assert any("spelled differently" in n for n in notes)


def test_host_verdict_other_name_differs():
    verdict, notes = cmp.host_verdict("Carex", "L.", TwName(1, "Typha", "L.", "genus"))
    assert verdict == "differs"
    assert "name" in notes[0]


# ── fetch_associations ──────────────────────────────────────────────────────────

def _basic(ba_id, co=501, obj=("AnatomicalPart", 687), otu=(9001, 1471869),
           label="rhizome: Typha L."):
    return {"id": ba_id, "relationship": "collected from",
            "subject": {"id": co, "type": "CollectionObject", "label": f"CO {co}"},
            "object": {"id": obj[1], "type": obj[0], "label": label},
            "subject_otu_id": otu[0], "object_otu_id": otu[1]}


def _plain(ba_id, co=501, obj=("AnatomicalPart", 687), rel=92):
    return {"id": ba_id, "biological_relationship_id": rel,
            "biological_association_subject_type": "CollectionObject",
            "biological_association_subject_id": co,
            "biological_association_object_type": obj[0],
            "biological_association_object_id": obj[1]}


def _routes(basic_rows, plain_rows):
    def handler(r):
        if r.url.path.endswith("/biological_associations/basic"):
            return httpx.Response(200, json=basic_rows)
        if r.url.path.endswith("/biological_associations"):
            return httpx.Response(200, json=plain_rows)
        pytest.fail(f"unexpected request {r.url}")
    return handler


def test_fetch_associations_joins_basic_and_plain(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(321383)], [_plain(321383)]))
    got = asyncio.run(cmp.fetch_associations([501]))
    t = got[321383]
    assert t.relationship_id == 92 and t.relationship_name == "collected from"
    assert (t.subject.type, t.subject.id, t.subject.otu_id) == ("CollectionObject", 501, 9001)
    assert (t.object.type, t.object.id, t.object.otu_id, t.object.label) == (
        "AnatomicalPart", 687, 1471869, "rhizome: Typha L.")


def test_fetch_associations_sends_the_ids_as_collection_object_filter(cfg, monkeypatch):
    seen = []

    def handler(r):
        seen.append(r.url.params.get_list("collection_object_id[]"))
        return _routes([], [])(r)
    _serve(monkeypatch, handler)
    asyncio.run(cmp.fetch_associations([501, 502]))
    assert seen == [["501", "502"], ["501", "502"]]


def test_fetch_associations_batches_at_100_ids(cfg, monkeypatch):
    batches = []

    def handler(r):
        batches.append(len(r.url.params.get_list("collection_object_id[]")))
        return _routes([], [])(r)
    _serve(monkeypatch, handler)
    asyncio.run(cmp.fetch_associations(list(range(1, 251))))
    assert sorted(batches) == [50, 50, 100, 100, 100, 100]


def test_fetch_associations_rejects_a_row_for_another_specimen(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1, co=999)], [_plain(1, co=999)]))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_lists_that_disagree_are_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1), _basic(2)], [_plain(1)]))
    with pytest.raises(TaxonWorksUnreachable, match="disagree"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_sides_that_disagree_are_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _routes([_basic(1)], [_plain(1, obj=("Otu", 5))]))
    with pytest.raises(TaxonWorksUnreachable, match="different sides"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_missing_otu_key_is_a_failure(cfg, monkeypatch):
    row = _basic(1)
    del row["object_otu_id"]
    _serve(monkeypatch, _routes([row], [_plain(1)]))
    with pytest.raises(TaxonWorksUnreachable, match="object_otu_id"):
        asyncio.run(cmp.fetch_associations([501]))


def test_fetch_associations_missing_relationship_id_is_a_failure(cfg, monkeypatch):
    row = _plain(1)
    row["biological_relationship_id"] = None
    _serve(monkeypatch, _routes([_basic(1)], [row]))
    with pytest.raises(TaxonWorksUnreachable, match="relationship id"):
        asyncio.run(cmp.fetch_associations([501]))


def test_empty_input_never_touches_the_network(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: pytest.fail("no request expected"))
    assert asyncio.run(cmp.fetch_associations([])) == {}
    assert asyncio.run(cmp.fetch_otu_names([])) == {}


# ── fetch_otu_names ─────────────────────────────────────────────────────────────

def _otu_routes(otus, names):
    def handler(r):
        if r.url.path.endswith("/otus"):
            return httpx.Response(200, json=otus)
        if r.url.path.endswith("/taxon_names"):
            return httpx.Response(200, json=names)
        pytest.fail(f"unexpected request {r.url}")
    return handler


def test_fetch_otu_names_follows_otu_to_taxon_name(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes(
        [{"id": 1471869, "taxon_name_id": 1655474}, {"id": 77, "taxon_name_id": None}],
        [{"id": 1655474, "cached": "Typha", "cached_author_year": "L.", "rank": "genus"}]))
    got = asyncio.run(cmp.fetch_otu_names([1471869, 77]))
    assert got[1471869] == TwName(1655474, "Typha", "L.", "genus")
    assert got[77] is None


def test_fetch_otu_names_missing_otu_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 1, "taxon_name_id": 10}],
                                    [{"id": 10, "cached": "Typha", "rank": "genus"}]))
    with pytest.raises(TaxonWorksUnreachable, match=r"OTU\(s\) \[2\]"):
        asyncio.run(cmp.fetch_otu_names([1, 2]))


def test_fetch_otu_names_missing_taxon_name_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 1, "taxon_name_id": 10}], []))
    with pytest.raises(TaxonWorksUnreachable, match=r"taxon name\(s\) \[10\]"):
        asyncio.run(cmp.fetch_otu_names([1]))


def test_fetch_otu_names_foreign_otu_is_a_failure(cfg, monkeypatch):
    _serve(monkeypatch, _otu_routes([{"id": 999, "taxon_name_id": 10}], []))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_otu_names([1]))
