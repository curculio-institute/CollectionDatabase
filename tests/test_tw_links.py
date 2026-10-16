"""tw_links.py — the one link / verify mechanism (spec: "The TaxonWorks link",
"tw_links.py", "Term colour"). No network: httpx.MockTransport stands in for TaxonWorks."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.taxonworks as tw
import app.services.tw_links as links
from app.config import AppConfig
from app.models import BiologicalRelationship
from app.models.base import _utcnow
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_links import KEYWORD, PREDICATE, RELATIONSHIP, TwTerm
from app.services.tw_links import VerifyOutcome
from tests.annotation_helpers import SANDBOX, SFG, make_term


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="tok"))


def _serve(monkeypatch, handler):
    """Route every tw_links / fetch_all_pages request through `handler(request)`."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(links, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


def _page(rows, total=None):
    headers = {"pagination-total": str(total)} if total is not None else {}
    return httpx.Response(200, json=rows, headers=headers)


def _kw(id_, name, **extra):
    return {"id": id_, "name": name, "type": "Keyword", "uri": None, "css_color": None,
            **extra}


# ── helpers in taxonworks.py ───────────────────────────────────────────────────

def test_configured_host(cfg):
    assert tw.configured_host() == SFG
    config_mod._instance.tw_base = ""
    assert tw.configured_host() == ""


def test_fetch_all_pages_repeated_rows_are_a_lookup_failure(cfg):
    """Review Focus 1: /controlled_vocabulary_terms and /data_attributes page without an
    ORDER BY, so a row can repeat on the next page while another is skipped. Counting
    received rows would call that complete; counting DISTINCT ids does not."""
    pages = {1: [_kw(1, "A"), _kw(2, "B")], 2: [_kw(2, "B"), _kw(3, "C")]}

    def handler(request):
        return _page(pages[int(request.url.params["page"])], total=4)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.fetch_all_pages(client, "/controlled_vocabulary_terms", {},
                                            what="the Keyword listing",
                                            check_row=lambda r: None, per_page=2)
    with pytest.raises(TaxonWorksUnreachable, match="3 distinct"):
        asyncio.run(run())


def test_fetch_all_pages_short_read_is_a_lookup_failure(cfg):
    def handler(request):
        page = int(request.url.params["page"])
        return _page([_kw(1, "A"), _kw(2, "B")] if page == 1 else [], total=5)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.fetch_all_pages(client, "/x", {}, what="the listing",
                                            check_row=lambda r: None, per_page=2)
    with pytest.raises(TaxonWorksUnreachable, match="2 of 5"):
        asyncio.run(run())


def test_get_with_retry_retries_a_transient_failure(cfg, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(tw, "_RETRY_BACKOFF", (0, 0))

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else _page([])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.get_with_retry(client, "https://x/api/v1/y", {})
    assert asyncio.run(run()).status_code == 200
    assert calls["n"] == 2


# ── fetch_candidates ───────────────────────────────────────────────────────────

def test_fetch_candidates_keywords(cfg, monkeypatch):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return _page([_kw(1764, "Adventive", css_color="#ff8800"), _kw(5, "Endemic")],
                     total=2)
    _serve(monkeypatch, handler)
    got = asyncio.run(links.fetch_candidates(KEYWORD))
    assert seen["type"] == "Keyword" and seen["project_token"] == "tok"
    assert [(t.id, t.name, t.css_color) for t in got] == [
        (1764, "Adventive", "#ff8800"), (5, "Endemic", None)]
    assert {t.host for t in got} == {SFG}


def test_fetch_candidates_wrong_type_is_a_lookup_failure(cfg, monkeypatch):
    """Unknown params are silently ignored (§5c): an unfiltered reply mixes types."""
    _serve(monkeypatch, lambda r: _page([_kw(1, "A"), {**_kw(2, "B"), "type": "Predicate"}]))
    with pytest.raises(TaxonWorksUnreachable, match="type filter"):
        asyncio.run(links.fetch_candidates(KEYWORD))


def test_fetch_candidates_names_a_rejected_token(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(401))
    with pytest.raises(TaxonWorksUnreachable, match="rejected the project token"):
        asyncio.run(links.fetch_candidates(PREDICATE))


def test_fetch_candidates_relationships(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: _page([{"id": 11, "name": "feeds on",
                                          "inverted_name": "food of"}], total=1))
    (t,) = asyncio.run(links.fetch_candidates(RELATIONSHIP))
    assert (t.kind, t.id, t.name) == (RELATIONSHIP, 11, "feeds on")


# ── link / unlink / state ──────────────────────────────────────────────────────

def test_link_sets_id_name_host_and_colour(session):
    t = make_term(session)
    warning = links.link(session, t, TwTerm(KEYWORD, 1764, "Adventive", None, "#ff8800", SFG))
    assert warning is None
    assert (t.tw_id, t.tw_name, t.tw_host, t.css_color) == (1764, "Adventive", SFG, "#ff8800")
    assert links.link_state(t, SFG) == "verified"
    assert links.link_state(t, SANDBOX) == "not_verified"


def test_link_refuses_an_id_held_by_another_term(session):
    make_term(session, name="First", tw_id=1764, tw_name="Adventive", tw_host=SFG)
    second = make_term(session, name="Second")
    with pytest.raises(ValueError, match="already linked to 'First'"):
        links.link(session, second, TwTerm(KEYWORD, 1764, "Adventive", host=SFG))


def test_link_refuses_the_wrong_kind(session):
    t = make_term(session)                         # a tag
    with pytest.raises(ValueError):
        links.link(session, t, TwTerm(PREDICATE, 3, "Altitude", host=SFG))


@pytest.mark.parametrize("raw", ["red", "#12345", 7])
def test_link_with_unusable_colour_keeps_link_and_warns(session, raw):
    """Review Focus 5."""
    t = make_term(session, css_color="#000")
    warning = links.link(session, t, TwTerm(KEYWORD, 9, "Adventive", None, raw, SFG))
    assert warning is not None and "not usable" in warning
    assert (t.tw_id, t.tw_host, t.css_color) == (9, SFG, None)


def test_link_refuses_a_term_without_host(session):
    t = make_term(session)
    with pytest.raises(ValueError, match="did not come from a TaxonWorks fetch"):
        links.link(session, t, TwTerm(KEYWORD, 9, "Adventive"))
    assert t.tw_id is None


def test_unlink_keeps_the_colour(session):
    t = make_term(session, tw_id=9, tw_name="Adventive", tw_host=SFG, css_color="#254d0d")
    links.unlink(session, t)
    assert (t.tw_id, t.tw_name, t.tw_host, t.css_color) == (None, None, None, "#254d0d")
    assert links.link_state(t, SFG) == "local_only"


def test_relationship_links_through_the_same_path(session):
    r = BiologicalRelationship(name="feeds on", created_at=_utcnow(), updated_at=_utcnow())
    session.add(r)
    session.flush()
    assert links.link(session, r, TwTerm(RELATIONSHIP, 11, "feeds on", host=SFG)) is None
    assert (r.taxonworks_id, r.tw_name, r.tw_host) == (11, "feeds on", SFG)
    links.unlink(session, r)
    assert (r.taxonworks_id, r.tw_name, r.tw_host) == (None, None, None)


# ── verify ─────────────────────────────────────────────────────────────────────

def test_apply_verification_refuses_unstamped_or_mixed_hosts(session):
    make_term(session, tw_id=1, tw_name="Adventive")
    with pytest.raises(ValueError, match="mixed or unstamped"):
        links.apply_verification(session, KEYWORD, [TwTerm(KEYWORD, 1, "Adventive")], host=SFG)
    with pytest.raises(ValueError, match="mixed or unstamped"):
        links.apply_verification(
            session, KEYWORD, [TwTerm(KEYWORD, 1, "Adventive", host=SANDBOX)], host=SFG)


def test_apply_verification_outcomes(session):
    same = make_term(session, name="Same", tw_id=1, tw_name="Same")
    renamed = make_term(session, name="Old", tw_id=2, tw_name="Old", tw_host=SFG,
                        css_color="#000")
    gone = make_term(session, name="Gone", tw_id=3, tw_name="Gone", tw_host=SFG)
    make_term(session, name="Local")
    out = links.apply_verification(session, KEYWORD, [
        TwTerm(KEYWORD, 1, "Same", None, "#abc", SFG),
        TwTerm(KEYWORD, 2, "New name", None, "#fff", SFG)], host=SFG)
    by_name = {o.name: o for o in out}
    assert set(by_name) == {"Same", "Old", "Gone"}            # local-only never checked
    assert by_name["Same"].state == "verified" and same.tw_host == SFG
    assert same.css_color == "#abc"
    assert by_name["Old"].state == "name_differs" and by_name["Old"].tw_name == "New name"
    assert renamed.tw_host is None and renamed.tw_name == "Old"
    assert renamed.css_color == "#fff"                          # colour follows TW anyway
    assert by_name["Gone"].state == "not_found"
    assert gone.tw_host is None and gone.tw_id == 3             # link never removed


def _outcome(t, tw_name="New name", state="name_differs", host=SFG):
    return VerifyOutcome(t.id, t.name, state, tw_name, None, host)


@pytest.mark.parametrize("choice,name,tw_name,host_set", [
    ("use_tw", "New name", "New name", True),
    ("keep_local", "Old", "New name", True),
    ("unlink", "Old", None, False)])
def test_resolve_name_difference(session, choice, name, tw_name, host_set):
    t = make_term(session, name="Old", tw_id=2, tw_name="Old")
    links.resolve_name_difference(session, t, _outcome(t), choice)
    assert (t.name, t.tw_name, t.tw_host is not None) == (name, tw_name, host_set)
    if host_set:
        assert t.tw_host == SFG


def test_resolve_refuses_other_outcomes_and_unlinked_rows(session):
    t = make_term(session, name="Old", tw_id=2, tw_name="Old")
    with pytest.raises(ValueError, match="name_differs"):
        links.resolve_name_difference(session, t, _outcome(t, state="verified"), "keep_local")
    with pytest.raises(ValueError, match="different row"):
        links.resolve_name_difference(session, t, VerifyOutcome(t.id + 99, "x", "name_differs",
                                                                "N", None, SFG), "keep_local")
    with pytest.raises(ValueError, match="no host"):
        links.resolve_name_difference(session, t, _outcome(t, host=""), "keep_local")
    local = make_term(session, name="Local")
    with pytest.raises(ValueError, match="not linked"):
        links.resolve_name_difference(session, local, _outcome(local), "keep_local")


def test_use_tw_name_refuses_a_local_name_clash(session):
    make_term(session, name="new name")
    t = make_term(session, name="Old", tw_id=2, tw_name="Old")
    with pytest.raises(ValueError, match="already exists"):
        links.resolve_name_difference(session, t, _outcome(t), "use_tw")


def test_verify_end_to_end(cfg, monkeypatch, session):
    t = make_term(session, tw_id=1764, tw_name="Adventive")
    _serve(monkeypatch, lambda r: _page([_kw(1764, "Adventive")], total=1))
    (o,) = asyncio.run(links.verify(session, KEYWORD))
    assert o.state == "verified" and t.tw_host == SFG


# ── fix round 1: fetch_all_pages / get_with_retry hardening ───────────────────

def _pull(handler, per_page=2):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.fetch_all_pages(client, "/x", {}, what="the listing",
                                            check_row=lambda r: None, per_page=per_page)
    return asyncio.run(run())


def test_fetch_all_pages_oversized_page_refused(cfg):
    with pytest.raises(TaxonWorksUnreachable, match="3 rows on a page of 2"):
        _pull(lambda r: _page([_kw(1, "A"), _kw(2, "B"), _kw(3, "C")]))


def test_fetch_all_pages_repeated_id_without_total_refused(cfg):
    pages = {1: [_kw(1, "A"), _kw(2, "B")], 2: [_kw(2, "B")]}
    with pytest.raises(TaxonWorksUnreachable, match="no pagination total"):
        _pull(lambda r: _page(pages[int(r.url.params["page"])]))


def test_fetch_all_pages_non_json_refused(cfg):
    with pytest.raises(TaxonWorksUnreachable, match="did not return JSON"):
        _pull(lambda r: httpx.Response(200, text="<html>maintenance</html>"))


def test_get_with_retry_wraps_non_transport_httpx_errors(cfg):
    def handler(request):
        raise httpx.TooManyRedirects("loop", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.get_with_retry(client, "https://x/api/v1/y", {})
    with pytest.raises(TaxonWorksUnreachable):
        asyncio.run(run())


def test_get_with_retry_wraps_invalid_url(cfg):
    async def run():
        async with httpx.AsyncClient() as client:
            return await tw.get_with_retry(client, "https://exa mple.org:99999999/y", {})
    with pytest.raises(TaxonWorksUnreachable):
        asyncio.run(run())
