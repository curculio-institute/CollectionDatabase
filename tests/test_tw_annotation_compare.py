"""Annotation compare (spec "Compare"): joins on tw_id only, verified terms only, every
TW response re-verified, event annotations compared per specimen."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.tw_annotation_compare as cmp
from app.config import AppConfig
from app.services import annotations as ann
from app.services.annotations import AnnotationItem
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_annotation_compare import TwDatum, TwTag
from tests.annotation_helpers import SANDBOX, SFG, make_event, make_specimen, make_term


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="t"))


def _serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cmp, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


def _run(session, specimens, **kw):
    base = dict(host=SFG, co_tags={}, co_data={}, event_of={}, ev_tags={}, ev_data={})
    base.update(kw)
    return cmp.compare_annotations(session, specimens, **base)


def _cats(result):
    return sorted((f.target, f.category, f.name) for f in result.findings)


def test_each_finding_category_for_a_specimen(session):
    tag = make_term(session, name="Adventive", tw_id=1764, tw_name="Adventive", tw_host=SFG)
    tag_tw_only = make_term(session, name="Endemic", tw_id=5, tw_name="Endemic", tw_host=SFG)
    da = make_term(session, "data_attribute", "Altitude", tw_id=3, tw_name="Altitude",
                   tw_host=SFG)
    co = make_specimen(session, "ANNX-00200")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "350 m")])
    result = _run(session, [(co.catalog_number, co.id, 501)],
                  co_tags={501: [TwTag(5, "Endemic"), TwTag(77, "Unknown on our side")]},
                  co_data={501: [TwDatum(3, "Altitude", "400 m"),
                                 TwDatum(None, "habitat (import attribute)", "x")]},
                  event_of={501: None})
    assert _cats(result) == [
        ("specimen", "missing_on_tw", "Adventive"),
        ("specimen", "only_on_tw", "Endemic"),
        ("specimen", "unlinked_on_tw", "Unknown on our side"),
        ("specimen", "unlinked_on_tw", "habitat (import attribute)"),
        ("specimen", "value_differs", "Altitude"),
    ]
    differs = next(f for f in result.findings if f.category == "value_differs")
    assert (differs.local_value, differs.tw_value) == ("350 m", "400 m")
    assert result.associations_not_compared is True
    _ = tag_tw_only


def test_unverified_term_is_listed_not_joined(session):
    """Review Focus 4: verified on sfg, compared against the sandbox."""
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG)
    co = make_specimen(session, "ANNX-00201")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    result = _run(session, [(co.catalog_number, co.id, 501)], host=SANDBOX,
                  co_tags={501: [TwTag(1764, "Something else on the sandbox")]},
                  event_of={501: None})
    assert result.unverified_terms == ("Adventive",)
    assert _cats(result) == [("specimen", "unlinked_on_tw", "Something else on the sandbox")]


def test_local_only_terms_are_never_compared(session):
    t = make_term(session, name="Needs re-mount")
    co = make_specimen(session, "ANNX-00202")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    assert _run(session, [(co.catalog_number, co.id, 501)], event_of={501: None}).findings == ()


def test_event_annotations_compare_per_specimen(session):
    t = make_term(session, "data_attribute", "EUNIS habitat", event=True, tw_id=3,
                  tw_name="EUNIS habitat", tw_host=SFG)
    ev = make_event(session)
    ann.set_annotations(session, "event", ev.id, [AnnotationItem(t.id, "G1.6")])
    a = make_specimen(session, "ANNX-00203", event_id=ev.id)
    b = make_specimen(session, "ANNX-00204", event_id=ev.id)
    result = _run(session, [(a.catalog_number, a.id, 501), (b.catalog_number, b.id, 502)],
                  event_of={501: 9001, 502: None},
                  ev_data={9001: [TwDatum(3, "EUNIS habitat", "G1.6")]})
    # a's TW event agrees; b's TW specimen has no event, so its event annotation is missing
    assert [(f.catalog_number, f.target, f.category) for f in result.findings] == [
        ("ANNX-00204", "event", "missing_on_tw")]


# ── fetch re-verification ──────────────────────────────────────────────────────

def test_fetch_tags_rejects_a_row_for_another_object(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 1, "tag_object_type": "CollectionObject", "tag_object_id": 999,
         "keyword_id": 5, "keyword": {"name": "Endemic"}}]))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_tags("CollectionObject", [501]))


def test_fetch_data_attributes_deduplicates_repeated_rows(cfg, monkeypatch):
    """/data_attributes has no ORDER BY (data_attributes_controller.rb:50): a row may
    repeat across pages. Distinct ids that reach the total are a complete read."""
    def row(i):
        return {"id": i, "attribute_subject_type": "CollectionObject",
                "attribute_subject_id": 501, "controlled_vocabulary_term_id": 3,
                "predicate_name": "Altitude", "value": f"{i}", "type": "InternalAttribute"}
    pages = {1: [row(1), row(2)], 2: [row(2), row(3)]}
    monkeypatch.setattr(cmp, "_PER_PAGE", 2)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, json=pages[int(r.url.params["page"])], headers={"pagination-total": "3"}))
    got = asyncio.run(cmp.fetch_data_attributes("CollectionObject", [501]))
    assert sorted(d.value for d in got[501]) == ["1", "2", "3"]


def test_fetch_event_ids_refuses_a_missing_object(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 501, "collecting_event_id": 9001}]))
    with pytest.raises(TaxonWorksUnreachable, match="502"):
        asyncio.run(cmp.fetch_event_ids([501, 502]))


def test_empty_input_never_touches_the_network(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: pytest.fail("no request expected"))
    assert asyncio.run(cmp.fetch_tags("CollectionObject", [])) == {}
    assert asyncio.run(cmp.fetch_event_ids([])) == {}


def test_multi_value_on_tw_is_a_difference(session):
    da = make_term(session, "data_attribute", "Altitude", tw_id=3, tw_name="Altitude",
                   tw_host=SFG)
    co = make_specimen(session, "ANNX-00205")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(da.id, "350 m")])
    result = _run(session, [(co.catalog_number, co.id, 501)],
                  co_data={501: [TwDatum(3, "Altitude", "350 m"),
                                 TwDatum(3, "Altitude", "400 m")]},
                  event_of={501: None})
    assert [(f.category, f.tw_value) for f in result.findings] == [
        ("value_differs", "350 m | 400 m")]


def test_fetch_event_ids_absent_key_refused_null_accepted(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[{"id": 501}]))
    with pytest.raises(TaxonWorksUnreachable, match="collecting_event_id"):
        asyncio.run(cmp.fetch_event_ids([501]))
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 501, "collecting_event_id": None}]))
    assert asyncio.run(cmp.fetch_event_ids([501])) == {501: None}
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 501, "collecting_event_id": "9"}]))
    with pytest.raises(TaxonWorksUnreachable, match="non-integer"):
        asyncio.run(cmp.fetch_event_ids([501]))


def test_fetch_data_attributes_refuses_malformed_internal_attribute(cfg, monkeypatch):
    base = {"id": 1, "attribute_subject_type": "CollectionObject",
            "attribute_subject_id": 501, "type": "InternalAttribute",
            "controlled_vocabulary_term_id": 3, "value": "x"}
    for bad in ({"controlled_vocabulary_term_id": None}, {"value": None}):
        _serve(monkeypatch, lambda r, bad=bad: httpx.Response(200, json=[{**base, **bad}]))
        with pytest.raises(TaxonWorksUnreachable):
            asyncio.run(cmp.fetch_data_attributes("CollectionObject", [501]))
