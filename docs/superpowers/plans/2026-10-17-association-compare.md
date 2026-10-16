# Biological-Association Compare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The TaxonWorks tab's Compare lists how the local biological associations (and their tags and data attributes) diverge from TaxonWorks, above all a worklist of associations to add there by hand.

**Architecture:** A new read-only module `app/services/tw_association_compare.py`, shaped like `tw_annotation_compare.py`: fetch functions (I/O, every response re-verified), a pure `compare_associations`, and `run_association_compare`. The TaxonWorks tab calls it as part of the existing Compare and renders a "Biological associations" section.

**Tech Stack:** Python, SQLAlchemy (SQLite), httpx (async), NiceGUI, pytest with `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-10-17-association-compare-design.md`

## Global Constraints

- Read-only against TaxonWorks: GET requests only, through `taxonworks.fetch_all_pages` (which uses `get_with_retry`). Nothing is written to TaxonWorks or stored locally.
- Tests make **no real network calls** (`httpx.MockTransport` via the module's `_client`), never open `data/collection.db`, never run `run.py` or the app.
- Every TaxonWorks behaviour cited in code carries its source at the pinned commit `897f385` (file / route).
- Relationships and annotation terms are joined on `tw_id` only, and only when verified on the configured host (`tw_id is not None and tw_host == host`). Never on names. OTU ids are never compared.
- Any lookup failure fails the whole association section; a partial list is never shown.
- A false "to add" is worse than no answer: on a specimen with any *Can't compare* association, its otherwise unmatched associations are *Can't compare* too.
- UI copy says "label", never "sticker"; plain sentences; `section-label` / existing tab conventions (`app/ui/tw_sync_tab.py` module docstring).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `conda run -n collection python -m pytest -q <path>`.

## Review Focus

1. **A specimen whose TaxonWorks association has no index row** (`/basic` then answers HTTP 500): must surface as a named lookup failure ("Biological associations were not checked: …"), not a crash or an empty list — covered by `get_with_retry` → `explain_tw_error`; Task 3 renders the error text.
2. **A large collection** (hundreds of specimens on TaxonWorks): requests are batched at 100 ids and the local query uses sub-selects, not a giant `IN (...)` list — Task 1 pins batching with a test.
3. **A field-occurrence host identified with a qualifier** (`cf.`): still compared by name, the qualifier shown as a note — Task 2 test `test_qualified_host_matches_with_note`.
4. **An association whose both sides are specimens on TaxonWorks**: compared once, anchored on the subject, never reported twice — Task 2 test `test_association_between_two_specimens_on_tw_is_compared_once`.
5. **A collection with no specimens on TaxonWorks yet**: the section says so instead of "0 confirmed" — Task 3 render branch on `checked_specimens == 0`.

---

### Task 1: TaxonWorks fetches and the host-name rule

**Files:**
- Create: `app/services/tw_association_compare.py`
- Test: `tests/test_tw_association_compare.py`

**Interfaces:**
- Consumes: `taxonworks.fetch_all_pages(client, path, params, *, what, check_row, per_page)`, `TaxonWorksUnreachable`; from `tw_annotation_compare`: `_batches`, `_foreign`, `_gather`; `taxa.binomial_key(name) -> str`, `taxa.authorship_matches(a, b) -> bool`.
- Produces:
  - `TwSide(type: str, id: int, otu_id: int | None, label: str)` (frozen dataclass)
  - `TwAssociation(id: int, relationship_id: int, relationship_name: str, subject: TwSide, object: TwSide)`
  - `TwName(taxon_name_id: int, cached: str, authorship: str | None, rank: str | None)`
  - `async fetch_associations(co_ids: list[int]) -> dict[int, TwAssociation]`
  - `async fetch_otu_names(otu_ids: list[int]) -> dict[int, TwName | None]` (None = the OTU has no taxon name)
  - `host_verdict(local_name: str, local_authorship: str | None, tw: TwName) -> tuple[str, tuple[str, ...]]` — verdict `"match"` | `"subgenus"` | `"differs"`, plus notes
  - `_client()` (patched in tests), `_PER_PAGE`, `_BATCH`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tw_association_compare.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_tw_association_compare.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tw_association_compare'`

- [ ] **Step 3: Write the module (fetches + host rule)**

Create `app/services/tw_association_compare.py`:

```python
"""Biological-association compare (spec docs/superpowers/specs/2026-10-17-association-
compare-design.md) — read-only, diagnostic.

TaxonWorks cannot import associations (``associatedTaxa`` is [Not mapped],
``docs/taxonworks.md`` §5b), so they are entered there by hand; this lists what diverges.
Built like ``tw_annotation_compare.py``: fetch functions (I/O, every response re-verified),
a pure ``compare_associations`` and ``run_association_compare``.

Verified against SpeciesFileGroup/taxonworks @ 897f385 (live probes on sfg, 2026-10-17):
* ``GET /api/v1/biological_associations/basic`` (``config/routes/api_v1.rb:127``,
  ``api/v1/basic.json.jbuilder``): per association ``subject``/``object`` ``{id, type,
  label}``, ``subject_otu_id``, ``object_otu_id`` and the relationship **name only**.
  For an anatomical-part side the ``*_otu_id`` is the OTU at the top of its origin chain.
* ``GET /api/v1/biological_associations`` (``api_v1.rb:128``,
  ``api/v1/_base_attributes.json.jbuilder``): ``biological_relationship_id`` and both
  sides' type and id. The two lists are joined on the association id.
* ``collection_object_id[]`` matches the specimen as subject OR object
  (``lib/queries/biological_association/filter.rb``, ``subject_object_facet``).
* ``GET /api/v1/otus?otu_id[]=`` rows carry ``taxon_name_id``; ``GET
  /api/v1/taxon_names?taxon_name_id[]=`` rows carry ``cached`` (no authorship),
  ``cached_author_year``, ``rank``.
* There is no ``anatomical_parts`` endpoint (``docs/taxonworks.md`` §5b) — the part is
  shown by its label, never compared; only the host's OTU name is.

OTU ids are never compared (§5c): the other side is compared by taxon name.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

import app.services.taxonworks as tw
from app.services import taxa
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_annotation_compare import _batches, _foreign, _gather

_TIMEOUT = httpx.Timeout(25.0)
_PER_PAGE = 500


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(frozen=True)
class TwSide:
    type: str               # CollectionObject | Otu | FieldOccurrence | AnatomicalPart | …
    id: int
    otu_id: int | None      # the OTU TaxonWorks indexes for this side (None: none)
    label: str              # TaxonWorks' display label, e.g. "rhizome: Typha L."


@dataclass(frozen=True)
class TwAssociation:
    id: int
    relationship_id: int
    relationship_name: str
    subject: TwSide
    object: TwSide


@dataclass(frozen=True)
class TwName:
    taxon_name_id: int
    cached: str             # TaxonWorks' composed name, no authorship
    authorship: str | None  # cached_author_year
    rank: str | None


async def fetch_associations(co_ids: list[int]) -> dict[int, TwAssociation]:
    """Every TaxonWorks association on these collection objects (as subject or object):
    ``/basic`` (sides, OTU ids, labels) joined on id with the plain index (relationship
    id). Both are re-verified; any disagreement between them is a lookup failure, never a
    finding."""
    out: dict[int, TwAssociation] = {}
    if not co_ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(co_ids))):
            wanted = set(batch)
            what_b = f"the TaxonWorks associations (basic) of {len(batch)} collection object(s)"
            what_p = f"the TaxonWorks associations of {len(batch)} collection object(s)"

            def involves(st, sid, ot, oid, wanted=wanted) -> bool:
                return ((st == "CollectionObject" and sid in wanted)
                        or (ot == "CollectionObject" and oid in wanted))

            def check_basic(row: dict, what=what_b) -> None:
                s, o = row.get("subject"), row.get("object")
                if not isinstance(s, dict) or not isinstance(o, dict):
                    raise TaxonWorksUnreachable(
                        f"{what} returned association #{row.get('id')} without its sides.")
                for side in (s, o):
                    if not isinstance(side.get("type"), str) or not _is_int(side.get("id")):
                        raise TaxonWorksUnreachable(
                            f"{what} returned association #{row.get('id')} with an "
                            f"incomplete side.")
                for key in ("subject_otu_id", "object_otu_id"):
                    if key not in row or (row[key] is not None and not _is_int(row[key])):
                        raise TaxonWorksUnreachable(
                            f"{what} returned association #{row.get('id')} without a "
                            f"usable {key}.")
                if not involves(s["type"], s["id"], o["type"], o["id"]):
                    raise _foreign(what, "BiologicalAssociation", row.get("id"))

            def check_plain(row: dict, what=what_p) -> None:
                if not _is_int(row.get("biological_relationship_id")):
                    raise TaxonWorksUnreachable(
                        f"{what} returned association #{row.get('id')} without a "
                        f"relationship id.")
                if not involves(row.get("biological_association_subject_type"),
                                row.get("biological_association_subject_id"),
                                row.get("biological_association_object_type"),
                                row.get("biological_association_object_id")):
                    raise _foreign(what, "BiologicalAssociation", row.get("id"))

            params = {"collection_object_id[]": batch}
            basic, plain = await _gather(
                tw.fetch_all_pages(client, "/biological_associations/basic", params,
                                   what=what_b, check_row=check_basic, per_page=_PER_PAGE),
                tw.fetch_all_pages(client, "/biological_associations", params,
                                   what=what_p, check_row=check_plain, per_page=_PER_PAGE))
            by_id = {r["id"]: r for r in plain}
            basic_ids = {r["id"] for r in basic}
            if basic_ids != set(by_id):
                raise TaxonWorksUnreachable(
                    f"{what_p}: the two association lists disagree (ids "
                    f"{sorted(basic_ids ^ set(by_id))}) — treated as a lookup failure, "
                    f"never as a difference.")
            for b in basic:
                p = by_id[b["id"]]
                sides_b = (b["subject"]["type"], b["subject"]["id"],
                           b["object"]["type"], b["object"]["id"])
                sides_p = (p.get("biological_association_subject_type"),
                           p.get("biological_association_subject_id"),
                           p.get("biological_association_object_type"),
                           p.get("biological_association_object_id"))
                if sides_b != sides_p:
                    raise TaxonWorksUnreachable(
                        f"{what_p}: association #{b['id']} has different sides in the two "
                        f"lists — treated as a lookup failure.")
                out[b["id"]] = TwAssociation(
                    id=b["id"], relationship_id=p["biological_relationship_id"],
                    relationship_name=str(b.get("relationship") or "").strip(),
                    subject=TwSide(b["subject"]["type"], b["subject"]["id"],
                                   b["subject_otu_id"],
                                   str(b["subject"].get("label") or "").strip()),
                    object=TwSide(b["object"]["type"], b["object"]["id"],
                                  b["object_otu_id"],
                                  str(b["object"].get("label") or "").strip()))
    return out


async def fetch_otu_names(otu_ids: list[int]) -> dict[int, TwName | None]:
    """OTU id → its TaxonWorks taxon name (None: the OTU has no taxon name). Every OTU and
    every taxon name asked for must come back."""
    if not otu_ids:
        return {}
    otu_tn: dict[int, int | None] = {}
    names: dict[int, TwName] = {}
    async with _client() as client:
        for batch in _batches(sorted(set(otu_ids))):
            wanted = set(batch)
            what = f"the TaxonWorks OTUs ({len(batch)} requested)"

            def check_otu(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "Otu", row.get("id"))
                tn = row.get("taxon_name_id", "absent")
                if tn == "absent" or (tn is not None and not _is_int(tn)):
                    raise TaxonWorksUnreachable(
                        f"{what} returned OTU #{row.get('id')} without a usable taxon_name_id.")

            rows = await tw.fetch_all_pages(client, "/otus", {"otu_id[]": batch}, what=what,
                                            check_row=check_otu, per_page=_PER_PAGE)
            for r in rows:
                otu_tn[r["id"]] = r["taxon_name_id"]
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return OTU(s) {missing} — treated as a lookup failure.")
        for batch in _batches(sorted({t for t in otu_tn.values() if t is not None})):
            wanted = set(batch)
            what = f"the TaxonWorks taxon names ({len(batch)} requested)"

            def check_name(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "TaxonName", row.get("id"))
                if not str(row.get("cached") or "").strip():
                    raise TaxonWorksUnreachable(
                        f"{what} returned taxon name #{row.get('id')} without a name.")

            rows = await tw.fetch_all_pages(client, "/taxon_names",
                                            {"taxon_name_id[]": batch}, what=what,
                                            check_row=check_name, per_page=_PER_PAGE)
            for r in rows:
                names[r["id"]] = TwName(
                    r["id"], " ".join(str(r["cached"]).split()),
                    str(r.get("cached_author_year") or "").strip() or None, r.get("rank"))
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return taxon name(s) {missing} — treated as a lookup "
                    f"failure.")
    return {otu: (names[tn] if tn is not None else None) for otu, tn in otu_tn.items()}


# ── The host-name rule (mirrors tw_sync.check_names, docs/taxonworks.md §5c) ───────

# A difference that disappears after this folding is a transliteration, not a different
# author (§5c: 'Schönherr' vs 'Schoenherr' is expected).
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                       "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def _ws(s: str | None) -> str:
    return " ".join((s or "").split())


def host_verdict(local_name: str, local_authorship: str | None,
                 tw_name: TwName) -> tuple[str, tuple[str, ...]]:
    """``("match" | "subgenus" | "differs", notes)`` for a local host name (bare, no
    authorship) against a TaxonWorks taxon name. A subgenus-only difference matches with a
    note (placement is unstable); two authorships that differ make the host differ (same
    spelling may be a homonym); a missing authorship on either side is a note."""
    ln, tn = _ws(local_name), _ws(tw_name.cached)
    notes: list[str] = []
    if ln == tn:
        verdict = "match"
    elif taxa.binomial_key(ln) == taxa.binomial_key(tn):
        verdict = "subgenus"
        notes.append(f"subgenus placement differs: TaxonWorks has '{tn}'")
    else:
        return "differs", (f"name: here '{ln}', TaxonWorks '{tn}'",)
    la, ta = _ws(local_authorship), _ws(tw_name.authorship)
    if not la or not ta:
        notes.append("authorship not compared (missing on one side)")
    elif taxa.authorship_matches(la, ta):
        pass
    elif taxa.authorship_matches(la.translate(_FOLD), ta.translate(_FOLD)):
        notes.append(f"authorship spelled differently: here '{la}', TaxonWorks '{ta}'")
    else:
        return "differs", (f"authorship: here '{la}', TaxonWorks '{ta}'",)
    return verdict, tuple(notes)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `conda run -n collection python -m pytest -q tests/test_tw_association_compare.py`
Expected: all pass. If `test_fetch_associations_batches_at_100_ids` fails, check that `_batches` (from `tw_annotation_compare`, batch size 100) is used for both endpoints.

- [ ] **Step 5: Commit**

```bash
git add app/services/tw_association_compare.py tests/test_tw_association_compare.py
git commit -m "Association compare: TaxonWorks fetches (re-verified) and the host-name rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The pure comparison, association annotations, and the run function

**Files:**
- Modify: `app/services/tw_association_compare.py` (append)
- Modify: `app/services/tw_annotation_compare.py:57-74` (finding target comment; drop `associations_not_compared`)
- Modify: `tests/test_tw_annotation_compare.py` (drop the `associations_not_compared` assertion)
- Modify: `app/ui/tw_sync_tab.py` (~line 656: delete the block reading `associations_not_compared`)
- Test: `tests/test_tw_association_compare.py` (append)

**Interfaces:**
- Consumes: Task 1's `TwSide`, `TwAssociation`, `TwName`, `host_verdict`, `fetch_associations`, `fetch_otu_names`; `tw_annotation_compare._diff(findings, cat, tw_object_id, target, local, tw_tags, tw_data, verified)`, `AnnotationFinding`, `TwTag`, `TwDatum`, `fetch_tags(object_type, ids)`, `fetch_data_attributes(object_type, ids)`; `tw_media_compare.specimens_on_tw(session, *, repository_id, index) -> (list[(cat, co_id, tw_id)], tuple[str,...])`; `annotations.list_terms(session)`; `field_occurrence.current_determination(session, fo)`; `taxa.compose_scientific_name(session, taxon)`.
- Produces:
  - `AssociationRow` (frozen): `catalog_number: str, tw_specimen_id: int, relationship: str, host: str, host_kind: str, local_id: int | None = None, tw_id: int | None = None, tw_host: str | None = None, reason: str = "", notes: tuple[str, ...] = (), annotations: tuple[str, ...] = ()`
  - `AssociationAnnotationFinding` (frozen): `catalog_number: str, tw_specimen_id: int, association: str, finding: AnnotationFinding`
  - `AssociationCompareResult` (frozen): `checked_specimens: int, confirmed_count: int, to_add, only_on_tw, host_differs, cant_compare: tuple[AssociationRow, ...], annotation_findings: tuple[AssociationAnnotationFinding, ...], unverified_terms: tuple[str, ...], not_on_tw_count: int, numberless_count: int, taxon_subject_count: int, ambiguous_catalog_numbers: tuple[str, ...]`
  - `compare_associations(session, *, repository_id: int, specimens: list[tuple[str, int, int]], ambiguous: tuple[str, ...], tw_assocs: dict[int, TwAssociation], names: dict[int, TwName | None], tw_tags: dict[int, list[TwTag]], tw_data: dict[int, list[TwDatum]], host: str) -> AssociationCompareResult`
  - `async run_association_compare(session, *, repository_id: int, index: CatalogIndex, host: str | None = None) -> AssociationCompareResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tw_association_compare.py`:

```python
# ── compare_associations (pure; temporary database, no network) ─────────────────

from app.models import BiologicalAssociation, BiologicalRelationship, Taxon  # noqa: E402
from app.models.base import _utcnow  # noqa: E402
from app.services import annotations as ann  # noqa: E402
from app.services import biological as bio  # noqa: E402
from app.services import field_occurrence as fo_svc  # noqa: E402
from app.services import specimens as spec_svc  # noqa: E402
from app.services.annotations import AnnotationItem  # noqa: E402
from app.services.tw_annotation_compare import TwDatum, TwTag  # noqa: E402
from app.services.tw_association_compare import TwAssociation, TwSide  # noqa: E402
from tests.annotation_helpers import (SANDBOX, SFG, make_event, make_specimen,  # noqa: E402
                                      make_term, species)

TYPHA = TwName(1655474, "Typha", "L.", "genus")


def _rel(session, name="collected from", tw_id=92, host=SFG):
    r = BiologicalRelationship(name=name, taxonworks_id=tw_id,
                               tw_name=name if tw_id is not None else None,
                               tw_host=host if tw_id is not None else None,
                               created_at=_utcnow(), updated_at=_utcnow())
    session.add(r)
    session.flush()
    return r


def _plant(session, genus="Typha", author="L."):
    t = Taxon(name_element=genus, scientific_name=genus, taxon_rank="genus",
              nomenclatural_code="ICN", scientific_name_authorship=author,
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def _on(session, co, rel, host_taxon):
    return bio.save_biological_association(
        session, collection_object_id=co.id, biological_relationship_id=rel.id,
        object_taxon_id=host_taxon.id)


def _tw(ba_id, otu=1471869, rel=92, co=501, obj_type="AnatomicalPart",
        label="rhizome: Typha L.", name="collected from"):
    return TwAssociation(ba_id, rel, name, TwSide("CollectionObject", co, 9001, f"CO {co}"),
                         TwSide(obj_type, 687, otu, label))


def _cmp(session, co, tw_assocs=(), names=None, **kw):
    base = dict(repository_id=co.repository_id,
                specimens=[(co.catalog_number, co.id, 501)], ambiguous=(),
                tw_assocs={t.id: t for t in tw_assocs},
                names={1471869: TYPHA} if names is None else names,
                tw_tags={}, tw_data={}, host=SFG)
    base.update(kw)
    return cmp.compare_associations(session, **base)


def test_anatomical_part_host_matches_by_its_otu_name(session):
    """JJPC-00102: TaxonWorks 'collected from → rhizome: Typha L.' (an anatomical part);
    local 'collected from → Typha L.'. The part is ignored, the OTU's name matches."""
    co = make_specimen(session, "ANNX-00300")
    _on(session, co, _rel(session), _plant(session))
    r = _cmp(session, co, [_tw(321383)])
    assert r.confirmed_count == 1
    assert (r.to_add, r.only_on_tw, r.host_differs, r.cant_compare) == ((), (), (), ())


def test_local_only_association_is_to_add(session):
    co = make_specimen(session, "ANNX-00301")
    a = _on(session, co, _rel(session), _plant(session))
    r = _cmp(session, co)
    (row,) = r.to_add
    assert (row.catalog_number, row.tw_specimen_id, row.local_id) == ("ANNX-00301", 501, a.id)
    assert (row.relationship, row.host, row.host_kind) == ("collected from", "Typha L.", "taxon")


def test_tw_only_association_shows_the_full_label(session):
    co = make_specimen(session, "ANNX-00302")
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.only_on_tw
    assert (row.tw_id, row.relationship, row.host, row.host_kind) == (
        9, "collected from", "rhizome: Typha L.", "AnatomicalPart")


def test_single_leftover_pair_is_host_differs(session):
    co = make_specimen(session, "ANNX-00303")
    _on(session, co, _rel(session), _plant(session, "Carex"))
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.host_differs
    assert (row.host, row.tw_host) == ("Carex L.", "rhizome: Typha L.")
    assert "name" in row.reason
    assert r.to_add == () and r.only_on_tw == ()


def test_several_leftovers_are_not_paired(session):
    co = make_specimen(session, "ANNX-00304")
    rel = _rel(session)
    _on(session, co, rel, _plant(session, "Carex"))
    _on(session, co, rel, _plant(session, "Juncus"))
    r = _cmp(session, co, [_tw(9)])
    assert len(r.to_add) == 2 and len(r.only_on_tw) == 1 and r.host_differs == ()


def test_two_local_against_one_on_tw(session):
    co = make_specimen(session, "ANNX-00305")
    rel, typha = _rel(session), _plant(session)
    _on(session, co, rel, typha)
    _on(session, co, rel, typha)
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1 and len(r.to_add) == 1


def test_authorship_difference_is_host_differs(session):
    co = make_specimen(session, "ANNX-00306")
    _on(session, co, _rel(session), _plant(session, "Typha", "Mill."))
    r = _cmp(session, co, [_tw(9)])
    (row,) = r.host_differs
    assert "authorship" in row.reason


def test_field_occurrence_host_uses_its_current_identification(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00307", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id)
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co, [_tw(9)])
    assert r.confirmed_count == 1


def test_qualified_host_matches_with_note(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00308", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id,
                                        identification_qualifier="cf.")
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co)
    (row,) = r.to_add
    assert any("cf." in n for n in row.notes)


def test_no_current_identification_cannot_be_compared(session):
    ev = make_event(session)
    co = make_specimen(session, "ANNX-00309", event_id=ev.id)
    fo = fo_svc.create_field_occurrence(session, collecting_event_id=ev.id,
                                        taxon_id=_plant(session).id)
    for det in fo.determinations:
        det.is_current = 0
    session.flush()
    bio.save_biological_association(session, collection_object_id=co.id,
                                    biological_relationship_id=_rel(session).id,
                                    object_field_occurrence_id=fo.id)
    r = _cmp(session, co, [_tw(9)])
    reasons = sorted(row.reason for row in r.cant_compare)
    assert len(reasons) == 2
    assert any("no current identification" in x for x in reasons)
    assert any("could not be compared" in x for x in reasons)   # the TW one, demoted
    assert r.only_on_tw == ()


def test_unverified_relationship_demotes_the_specimen(session):
    co = make_specimen(session, "ANNX-00310")
    _on(session, co, _rel(session, "feeds on", tw_id=50, host=SANDBOX), _plant(session))
    _on(session, co, _rel(session), _plant(session, "Carex"))
    r = _cmp(session, co, [_tw(9)])
    assert r.to_add == () and r.only_on_tw == () and r.host_differs == ()
    assert len(r.cant_compare) == 3
    assert any("not verified" in row.reason for row in r.cant_compare)


def test_tw_otu_without_taxon_name_cannot_be_compared(session):
    co = make_specimen(session, "ANNX-00311")
    r = _cmp(session, co, [_tw(9, otu=77)], names={77: None})
    (row,) = r.cant_compare
    assert "no taxon name" in row.reason


def test_specimen_as_object(session):
    """A local specimen that is the OBJECT (e.g. a parasitoid reared from it): anchored
    on the object; the other side is the subject specimen's current identification."""
    co = make_specimen(session, "ANNX-00312")
    parasitoid = make_specimen(session, "ANNX-00313")      # not on TaxonWorks
    rel = _rel(session, "parasitoid of", tw_id=60)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_collection_object_id=parasitoid.id,
                                      object_collection_object_id=co.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    t = TwAssociation(9, 60, "parasitoid of",
                      TwSide("CollectionObject", 777, 5, "CO 777"),
                      TwSide("CollectionObject", 501, 9001, "CO 501"))
    r = _cmp(session, co, [t],
             names={5: TwName(2, "Otiorhynchus crypticus", None, "species")})
    assert r.confirmed_count == 1


def test_association_between_two_specimens_on_tw_is_compared_once(session):
    a = make_specimen(session, "ANNX-00314")
    b = make_specimen(session, "ANNX-00315")
    rel = _rel(session, "parasitoid of", tw_id=60)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_collection_object_id=a.id,
                                      object_collection_object_id=b.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    r = _cmp(session, a, [], specimens=[(a.catalog_number, a.id, 501),
                                        (b.catalog_number, b.id, 502)])
    assert len(r.to_add) == 1
    assert r.to_add[0].catalog_number == "ANNX-00314"      # anchored on the subject


def test_counts_of_what_was_not_compared(session):
    co = make_specimen(session, "ANNX-00316")
    rel, typha = _rel(session), _plant(session)
    not_on_tw = make_specimen(session, "ANNX-00317")
    _on(session, not_on_tw, rel, typha)
    numberless = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number=None,
        repository_id=co.repository_id)
    session.flush()
    _on(session, numberless, rel, typha)
    session.add(BiologicalAssociation(biological_relationship_id=rel.id,
                                      subject_taxon_id=species(session).id,
                                      object_collection_object_id=co.id,
                                      created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    r = _cmp(session, co)
    assert (r.not_on_tw_count, r.numberless_count, r.taxon_subject_count) == (1, 1, 1)
    assert r.checked_specimens == 1


# ── association annotations ─────────────────────────────────────────────────────

def test_matched_association_annotations_are_compared(session):
    co = make_specimen(session, "ANNX-00320")
    a = _on(session, co, _rel(session), _plant(session))
    tag = make_term(session, name="Adventive", association=True, tw_id=1764,
                    tw_name="Adventive", tw_host=SFG)
    ann.set_annotations(session, "association", a.id, [AnnotationItem(tag.id)])
    r = _cmp(session, co, [_tw(9)], tw_tags={9: [TwTag(5, "Unknown here")]})
    got = sorted((f.finding.target, f.finding.category, f.finding.name)
                 for f in r.annotation_findings)
    assert got == [("association", "missing_on_tw", "Adventive"),
                   ("association", "unlinked_on_tw", "Unknown here")]
    assert r.annotation_findings[0].tw_specimen_id == 501
    assert r.annotation_findings[0].association == "collected from → Typha L."


def test_to_add_lists_its_annotations(session):
    co = make_specimen(session, "ANNX-00321")
    a = _on(session, co, _rel(session), _plant(session))
    tag = make_term(session, name="Adventive", association=True, tw_id=1764,
                    tw_name="Adventive", tw_host=SFG)
    da = make_term(session, "data_attribute", "Plant part", association=True)
    ann.set_annotations(session, "association", a.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "rhizome")])
    (row,) = _cmp(session, co).to_add
    assert row.annotations == ("tag Adventive",
                               "data attribute Plant part = rhizome (local only — never sent)")


def test_only_on_tw_lists_its_annotations(session):
    co = make_specimen(session, "ANNX-00322")
    r = _cmp(session, co, [_tw(9)], tw_tags={9: [TwTag(1764, "Adventive")]},
             tw_data={9: [TwDatum(3, "Plant part", "rhizome")]})
    (row,) = r.only_on_tw
    assert row.annotations == ("tag Adventive", "data attribute Plant part = rhizome")


def test_unverified_terms_are_listed(session):
    co = make_specimen(session, "ANNX-00323")
    make_term(session, name="Adventive", association=True, tw_id=1764,
              tw_name="Adventive", tw_host=SANDBOX)
    assert _cmp(session, co).unverified_terms == ("Adventive",)


# ── run_association_compare (wiring; fetchers replaced) ─────────────────────────

def test_run_fetches_names_for_the_other_side_and_annotations_by_association(
        session, monkeypatch):
    from app.services.tw_compare import CatalogIndex, TwCatalogEntry
    co = make_specimen(session, "ANNX-00330")
    entry = TwCatalogEntry("ANNX-00330", "00330", 501, None, "ANNX")
    index = CatalogIndex((entry,), {"ANNX-00330": (entry,)})
    calls = {}

    async def fake_assocs(ids):
        calls["assocs"] = ids
        return {9: _tw(9)}

    async def fake_names(ids):
        calls["names"] = ids
        return {1471869: TYPHA}

    async def fake_tags(object_type, ids):
        calls["tags"] = (object_type, ids)
        return {}

    async def fake_data(object_type, ids):
        calls["data"] = (object_type, ids)
        return {}
    monkeypatch.setattr(cmp, "fetch_associations", fake_assocs)
    monkeypatch.setattr(cmp, "fetch_otu_names", fake_names)
    monkeypatch.setattr(cmp.ann_cmp, "fetch_tags", fake_tags)
    monkeypatch.setattr(cmp.ann_cmp, "fetch_data_attributes", fake_data)
    r = asyncio.run(cmp.run_association_compare(
        session, repository_id=co.repository_id, index=index, host=SFG))
    assert calls == {"assocs": [501], "names": [1471869],
                     "tags": ("BiologicalAssociation", [9]),
                     "data": ("BiologicalAssociation", [9])}
    assert len(r.only_on_tw) == 1
```

In `tests/test_tw_annotation_compare.py`, delete the line `    assert result.associations_not_compared is True` in `test_each_finding_category_for_a_specimen`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_tw_association_compare.py`
Expected: the Task 1 tests pass; the new ones FAIL with `AttributeError: module 'app.services.tw_association_compare' has no attribute 'compare_associations'`.

- [ ] **Step 3: Drop `associations_not_compared` from the annotation compare**

In `app/services/tw_annotation_compare.py`, change the `AnnotationFinding.target` comment and remove the field from `AnnotationCompareResult`:

```python
@dataclass(frozen=True)
class AnnotationFinding:
    catalog_number: str
    tw_object_id: int
    target: str                    # specimen | event | association
    kind: str                      # tag | data_attribute
    category: str                  # missing_on_tw | only_on_tw | value_differs | unlinked_on_tw
    name: str
    local_value: str | None = None
    tw_value: str | None = None


@dataclass(frozen=True)
class AnnotationCompareResult:
    checked_count: int
    findings: tuple[AnnotationFinding, ...]
    unverified_terms: tuple[str, ...]        # linked, not verified here: not compared
    ambiguous_catalog_numbers: tuple[str, ...] = ()
```

In the same step, delete the block in `app/ui/tw_sync_tab.py` (`_render_annotation_results`, ~line 656) that reads the dropped field, so the tab never breaks between tasks:

```python
        if ann_result.associations_not_compared:
            ui.label("Annotations on biological associations are not compared — "
                     "TaxonWorks cannot import associations.").classes("text-xs mt-1") \
                .style("color:var(--tp-base-soft)")
```

- [ ] **Step 4: Append the comparison and run function**

Append to `app/services/tw_association_compare.py` (and add the imports to the top of the file):

```python
# --- add to the imports at the top of the module ---
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Annotation, AnnotationTerm, BiologicalAssociation, CollectionObject,
                        Taxon)
from app.services import annotations as ann
from app.services import field_occurrence as fo_svc
from app.services import tw_annotation_compare as ann_cmp
from app.services import tw_media_compare
from app.services.tw_annotation_compare import AnnotationFinding
from app.services.tw_compare import CatalogIndex
```

```python
# ── Result shapes ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssociationRow:
    catalog_number: str
    tw_specimen_id: int                 # for the "open in TaxonWorks" specimen link
    relationship: str
    host: str                           # the other side, as recorded on its side
    host_kind: str                      # taxon | field occurrence | specimen | TW type
    local_id: int | None = None
    tw_id: int | None = None
    tw_host: str | None = None          # Host differs: TaxonWorks' side
    reason: str = ""
    notes: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()   # display lines (To add / Only on TaxonWorks)


@dataclass(frozen=True)
class AssociationAnnotationFinding:
    catalog_number: str
    tw_specimen_id: int
    association: str                    # "collected from → Typha L."
    finding: AnnotationFinding          # target "association", tw_object_id = TW association


@dataclass(frozen=True)
class AssociationCompareResult:
    checked_specimens: int
    confirmed_count: int
    to_add: tuple[AssociationRow, ...]
    only_on_tw: tuple[AssociationRow, ...]
    host_differs: tuple[AssociationRow, ...]
    cant_compare: tuple[AssociationRow, ...]
    annotation_findings: tuple[AssociationAnnotationFinding, ...]
    unverified_terms: tuple[str, ...]
    not_on_tw_count: int
    numberless_count: int
    taxon_subject_count: int
    ambiguous_catalog_numbers: tuple[str, ...]


# ── Local side ───────────────────────────────────────────────────────────────────

def _current_taxon(co: CollectionObject) -> Taxon | None:
    # Same one-line `is_current == 1` scan tw_compare / tw_sync each keep privately.
    for det in co.determinations:
        if det.is_current == 1:
            return det.taxon
    return None


def _display(session: Session, t: Taxon) -> str:
    return " ".join(x for x in (taxa.compose_scientific_name(session, t),
                                (t.scientific_name_authorship or "").strip()) if x)


@dataclass(frozen=True)
class _Local:
    a: BiologicalAssociation
    anchor_co_id: int
    anchor_is_subject: bool
    taxon: Taxon | None
    host: str
    host_kind: str
    notes: tuple[str, ...]


def _local_entry(session: Session, a: BiologicalAssociation, anchor_co_id: int,
                 anchor_is_subject: bool) -> _Local:
    notes: tuple[str, ...] = ()
    if anchor_is_subject and a.object_taxon is not None:
        t = a.object_taxon
        return _Local(a, anchor_co_id, True, t, _display(session, t), "taxon", notes)
    if anchor_is_subject and a.object_field_occurrence is not None:
        det = fo_svc.current_determination(session, a.object_field_occurrence)
        t = det.taxon if det is not None else None
        if det is not None and det.identification_qualifier:
            notes = (f"identified with qualifier '{det.identification_qualifier}'",)
        return _Local(a, anchor_co_id, True, t,
                      _display(session, t) if t else "no current identification",
                      "field occurrence", notes)
    co = a.object_collection_object if anchor_is_subject else a.subject_collection_object
    t = _current_taxon(co)
    label = f"specimen {co.catalog_number or '(no catalog number)'}"
    return _Local(a, anchor_co_id, anchor_is_subject, t,
                  f"{label} ({_display(session, t)})" if t else label, "specimen", notes)


def _verified(rel, host: str) -> bool:
    return rel.tw_id is not None and rel.tw_host == host


# ── TaxonWorks side ──────────────────────────────────────────────────────────────

def _tw_anchor_is_subject(t: TwAssociation, tw_co_ids: set[int]) -> bool:
    """The anchor is the subject when the subject is one of our specimens, else the
    object (fetch_associations verified one of them is)."""
    return t.subject.type == "CollectionObject" and t.subject.id in tw_co_ids


def _other(t: TwAssociation, anchor_is_subject: bool) -> TwSide:
    return t.object if anchor_is_subject else t.subject


# ── Annotation display lines ─────────────────────────────────────────────────────

def _local_annotation_lines(anns: list, host: str) -> tuple[str, ...]:
    out = []
    for a in anns:
        t = a.term
        noun = "tag" if t.kind == "tag" else "data attribute"
        if t.tw_id is not None and t.tw_host == host:
            name, state = t.tw_name, ""
        elif t.tw_id is None:
            name, state = t.name, " (local only — never sent)"
        else:
            name, state = t.name, " (link not verified here)"
        out.append(f"{noun} {name}" + (f" = {a.value}" if a.value else "") + state)
    return tuple(out)


def _tw_annotation_lines(tags: list, data: list) -> tuple[str, ...]:
    return tuple([f"tag {t.name}" for t in tags]
                 + [f"data attribute {d.name} = {d.value}" for d in data])


# ── The comparison ───────────────────────────────────────────────────────────────

_DEMOTED = "another association on this specimen could not be compared"


def compare_associations(session: Session, *, repository_id: int,
                         specimens: list[tuple[str, int, int]], ambiguous: tuple[str, ...],
                         tw_assocs: dict[int, TwAssociation],
                         names: dict[int, TwName | None],
                         tw_tags: dict, tw_data: dict, host: str) -> AssociationCompareResult:
    """Pure computation, no I/O (spec "What is compared"). ``specimens`` is
    ``(catalog_number, local co id, TW co id)`` from ``tw_media_compare.specimens_on_tw``."""
    on_tw = {co_id: (cat, tw_id) for cat, co_id, tw_id in specimens}
    tw_co_ids = {tw_id for _cat, _co, tw_id in specimens}
    tw_to_co = {tw_id: co_id for _cat, co_id, tw_id in specimens}
    ambiguous_set = set(ambiguous)
    repo_ids = select(CollectionObject.id).where(CollectionObject.repository_id == repository_id)

    terms = ann.list_terms(session)
    verified_terms = {t.id: t for t in terms if t.tw_id is not None and t.tw_host == host}
    unverified_terms = tuple(sorted({t.name for t in terms
                                     if t.tw_id is not None and t.tw_host != host}))

    # ── local associations touching this collection (sub-selects: no IN-list limit)
    local_rows = (session.query(BiologicalAssociation)
                  .filter((BiologicalAssociation.subject_collection_object_id.in_(repo_ids))
                          | (BiologicalAssociation.object_collection_object_id.in_(repo_ids))
                          | ((BiologicalAssociation.subject_taxon_id.isnot(None))
                             & BiologicalAssociation.object_collection_object_id.is_(None)))
                  .order_by(BiologicalAssociation.id).all())
    not_on_tw = numberless = taxon_subject = 0
    locals_by_anchor: dict[int, list[_Local]] = defaultdict(list)
    for a in local_rows:
        if a.subject_taxon_id is not None:
            taxon_subject += 1
            continue
        s, o = a.subject_collection_object_id, a.object_collection_object_id
        if s in on_tw:
            locals_by_anchor[s].append(_local_entry(session, a, s, True))
        elif o is not None and o in on_tw:
            locals_by_anchor[o].append(_local_entry(session, a, o, False))
        else:
            side = a.subject_collection_object if a.subject_collection_object.repository_id \
                == repository_id else a.object_collection_object
            if side.catalog_number is None:
                numberless += 1
            elif side.catalog_number not in ambiguous_set:
                not_on_tw += 1

    tw_by_anchor: dict[int, list[TwAssociation]] = defaultdict(list)
    for t in sorted(tw_assocs.values(), key=lambda x: x.id):
        anchor_subject = _tw_anchor_is_subject(t, tw_co_ids)
        anchor_tw = t.subject.id if anchor_subject else t.object.id
        tw_by_anchor[tw_to_co[anchor_tw]].append(t)

    # local annotations of every association involved, batch-loaded, ordered like
    # annotations_for (tags first, then by name)
    local_ids = sorted({e.a.id for es in locals_by_anchor.values() for e in es})
    anns_by_ba: dict[int, list] = defaultdict(list)
    if local_ids:
        for an in (session.query(Annotation)
                   .join(AnnotationTerm, Annotation.term_id == AnnotationTerm.id)
                   .filter(Annotation.biological_association_id.in_(local_ids))
                   .order_by(AnnotationTerm.kind.desc(), func.lower(AnnotationTerm.name))):
            anns_by_ba[an.biological_association_id].append(an)

    confirmed = 0
    to_add: list[AssociationRow] = []
    only: list[AssociationRow] = []
    differs: list[AssociationRow] = []
    cant: list[AssociationRow] = []
    ann_findings: list[AssociationAnnotationFinding] = []

    for co_id in sorted(set(locals_by_anchor) | set(tw_by_anchor),
                        key=lambda c: on_tw[c][0]):
        cat, tw_specimen = on_tw[co_id]

        def local_row(e: _Local, **kw) -> AssociationRow:
            return AssociationRow(cat, tw_specimen, e.a.biological_relationship.name, e.host,
                                  e.host_kind, local_id=e.a.id, notes=e.notes,
                                  annotations=_local_annotation_lines(
                                      anns_by_ba.get(e.a.id, []), host), **kw)

        def tw_row(t: TwAssociation, anchor_subject: bool, **kw) -> AssociationRow:
            other = _other(t, anchor_subject)
            return AssociationRow(cat, tw_specimen, t.relationship_name, other.label,
                                  other.type, tw_id=t.id,
                                  annotations=_tw_annotation_lines(
                                      tw_tags.get(t.id, []), tw_data.get(t.id, [])), **kw)

        # comparable vs can't-compare, both sides
        ls, ts = [], []
        blocked = False
        for e in locals_by_anchor.get(co_id, []):
            rel = e.a.biological_relationship
            if not _verified(rel, host):
                why = (f"relationship '{rel.name}' is not linked to TaxonWorks"
                       if rel.tw_id is None else
                       f"relationship '{rel.name}' is linked but not verified on {host}")
                cant.append(local_row(e, reason=why))
                blocked = True
            elif e.taxon is None:
                cant.append(local_row(e, reason="the other side has no current identification"))
                blocked = True
            else:
                ls.append(e)
        for t in tw_by_anchor.get(co_id, []):
            anchor_subject = _tw_anchor_is_subject(t, tw_co_ids)
            other = _other(t, anchor_subject)
            if other.otu_id is None:
                cant.append(tw_row(t, anchor_subject,
                                   reason="TaxonWorks gives no OTU for the other side"))
                blocked = True
            elif names[other.otu_id] is None:
                cant.append(tw_row(t, anchor_subject,
                                   reason=f"TaxonWorks OTU {other.otu_id} has no taxon name"))
                blocked = True
            else:
                ts.append((t, anchor_subject, names[other.otu_id]))

        # pair: exact names first, then subgenus-only; ascending ids within a tier
        paired_l: set[int] = set()
        paired_t: set[int] = set()
        for tier in ("match", "subgenus"):
            for e in ls:
                if e.a.id in paired_l:
                    continue
                name = taxa.compose_scientific_name(session, e.taxon)
                for t, anchor_subject, tw_name in ts:
                    if t.id in paired_t or anchor_subject != e.anchor_is_subject:
                        continue
                    if t.relationship_id != e.a.biological_relationship.tw_id:
                        continue
                    verdict, _notes = host_verdict(name, e.taxon.scientific_name_authorship,
                                                   tw_name)
                    if verdict != tier:
                        continue
                    paired_l.add(e.a.id)
                    paired_t.add(t.id)
                    confirmed += 1
                    before = []
                    ann_cmp._diff(before, cat, t.id, "association", anns_by_ba.get(e.a.id, []),
                                  tw_tags.get(t.id, []), tw_data.get(t.id, []), verified_terms)
                    label = f"{e.a.biological_relationship.name} → {e.host}"
                    ann_findings.extend(AssociationAnnotationFinding(cat, tw_specimen, label, f)
                                        for f in before)
                    break

        left_l = [e for e in ls if e.a.id not in paired_l]
        left_t = [x for x in ts if x[0].id not in paired_t]
        if blocked:
            cant.extend(local_row(e, reason=_DEMOTED) for e in left_l)
            cant.extend(tw_row(t, s, reason=_DEMOTED) for t, s, _n in left_t)
            continue
        groups: dict[tuple[bool, int], tuple[list, list]] = defaultdict(lambda: ([], []))
        for e in left_l:
            groups[(e.anchor_is_subject, e.a.biological_relationship.tw_id)][0].append(e)
        for x in left_t:
            groups[(x[1], x[0].relationship_id)][1].append(x)
        for (_s, _rel), (gl, gt) in groups.items():
            if len(gl) == 1 and len(gt) == 1:
                e, (t, anchor_subject, tw_name) = gl[0], gt[0]
                _v, why = host_verdict(taxa.compose_scientific_name(session, e.taxon),
                                       e.taxon.scientific_name_authorship, tw_name)
                differs.append(local_row(e, tw_id=t.id,
                                         tw_host=_other(t, anchor_subject).label,
                                         reason="; ".join(why)))
                continue
            to_add.extend(local_row(e) for e in gl)
            only.extend(tw_row(t, s) for t, s, _n in gt)

    return AssociationCompareResult(
        checked_specimens=len(specimens), confirmed_count=confirmed,
        to_add=tuple(to_add), only_on_tw=tuple(only), host_differs=tuple(differs),
        cant_compare=tuple(cant), annotation_findings=tuple(ann_findings),
        unverified_terms=unverified_terms, not_on_tw_count=not_on_tw,
        numberless_count=numberless, taxon_subject_count=taxon_subject,
        ambiguous_catalog_numbers=tuple(ambiguous))


async def run_association_compare(session: Session, *, repository_id: int,
                                  index: CatalogIndex,
                                  host: str | None = None) -> AssociationCompareResult:
    """I/O + computation — the entry point the TaxonWorks tab calls."""
    host = tw.configured_host() if host is None else host
    specimens, ambiguous = tw_media_compare.specimens_on_tw(
        session, repository_id=repository_id, index=index)
    tw_co_ids = sorted({tw_id for _cat, _co, tw_id in specimens})
    tw_assocs = await fetch_associations(tw_co_ids)
    co_set = set(tw_co_ids)
    otu_ids = sorted({_other(t, _tw_anchor_is_subject(t, co_set)).otu_id
                      for t in tw_assocs.values()} - {None})
    ba_ids = sorted(tw_assocs)
    names, tags, data = await _gather(
        fetch_otu_names(otu_ids),
        ann_cmp.fetch_tags("BiologicalAssociation", ba_ids),
        ann_cmp.fetch_data_attributes("BiologicalAssociation", ba_ids))
    return compare_associations(session, repository_id=repository_id, specimens=specimens,
                                ambiguous=ambiguous, tw_assocs=tw_assocs, names=names,
                                tw_tags=tags, tw_data=data, host=host)
```

Note on the counts loop: `a.subject_collection_object` exists for every non-taxon-subject row (the subject exclusive arc), so `side` is the subject when it belongs to this collection, else the object.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n collection python -m pytest -q tests/test_tw_association_compare.py tests/test_tw_annotation_compare.py`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `conda run -n collection python -m pytest -q`
Expected: all pass (no test may still read `associations_not_compared`).

- [ ] **Step 7: Commit**

```bash
git add app/services/tw_association_compare.py app/services/tw_annotation_compare.py \
        app/ui/tw_sync_tab.py tests/test_tw_association_compare.py tests/test_tw_annotation_compare.py
git commit -m "Association compare: match local and TaxonWorks associations, incl. their tags and data attributes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The "Biological associations" section in the TaxonWorks tab, and the docs

**Files:**
- Modify: `app/ui/tw_sync_tab.py` (imports ~line 97-104; `_render_annotation_results` ~615; the Compare run ~1897-1920; the result render ~1786)
- Modify: `docs/taxonworks.md` (§5c: new subsection), `docs/app_structure.md` (service-layer module table), `docs/superpowers/specs/2026-10-14-annotations-design.md` (the "Biological-association annotations: `associations_not_compared`" line)

**Interfaces:**
- Consumes: Task 2's `run_association_compare(session, *, repository_id, index)`, `AssociationCompareResult`, `AssociationRow`, `AssociationAnnotationFinding`; existing `_ANN_CATEGORY`, `_open_catalog_numbers`, `tw_compare.edit_url`, `id_svc.catalog_label`, `tw_svc.TaxonWorksUnreachable`.
- Produces: `_render_association_results(assoc_result, error: str | None)` inside the tab builder; `state["compare"][repo_id]["associations"]` and `["associations_error"]`.

- [ ] **Step 1: Import the module**

In `app/ui/tw_sync_tab.py`, next to `import app.services.tw_annotation_compare as tw_annotation_compare`, add:

```python
import app.services.tw_association_compare as tw_association_compare
```

- [ ] **Step 2: Add the renderer**

Directly after `_render_annotation_results`, add:

```python
    def _render_association_results(assoc_result, error: str | None) -> None:
        """Spec 2026-10-17 (association compare): diagnostic only — associations are
        entered on TaxonWorks by hand; Compare again after adding them."""
        soft = "color:var(--tp-base-soft)"
        if assoc_result is None:
            ui.label("Biological associations were not checked"
                     + (f": {error}" if error else ".")).classes("text-xs mt-2").style(soft)
            return
        ui.label("Biological associations").classes("text-sm font-semibold mt-3")
        if assoc_result.checked_specimens == 0:
            ui.label("No specimens on TaxonWorks yet — nothing to compare associations "
                     "against.").classes("text-xs").style(soft)
        else:
            ui.label(f"{assoc_result.confirmed_count} association(s) confirmed on TaxonWorks "
                     f"across {assoc_result.checked_specimens} specimen(s).") \
                .classes("text-xs").style(soft)
        for title, rows in (("To add on TaxonWorks", assoc_result.to_add),
                            ("Only on TaxonWorks", assoc_result.only_on_tw),
                            ("Host differs", assoc_result.host_differs),
                            ("Can't compare", assoc_result.cant_compare)):
            if not rows:
                continue
            with ui.expansion(f"{title} ({len(rows)})").classes("w-full"):
                for r in rows:
                    text = (f"{id_svc.catalog_label(r.catalog_number)} · {r.relationship} → "
                            f"{r.host} ({r.host_kind})")
                    if r.tw_host:
                        text += f" — TaxonWorks: {r.tw_host}"
                    if r.reason:
                        text += f" — {r.reason}"
                    with ui.row().classes("items-center gap-2"):
                        ui.label(text).classes("text-xs")
                        ui.button("show here", icon="open_in_new",
                                  on_click=lambda _, c=r.catalog_number: _open_catalog_numbers(
                                      [c], f"Associations — {c}")) \
                            .props("flat dense no-caps size=sm")
                        ui.link("open in TaxonWorks", tw_compare.edit_url(r.tw_specimen_id),
                                new_tab=True).classes("text-xs")
                    for line in r.notes + r.annotations:
                        ui.label(line).classes("text-xs ml-6").style(soft)
        if assoc_result.annotation_findings:
            n = len(assoc_result.annotation_findings)
            with ui.expansion(f"Tags and data attributes on matched associations ({n})") \
                    .classes("w-full"):
                for af in assoc_result.annotation_findings:
                    f = af.finding
                    vals = " · ".join(x for x in (
                        f"here: {f.local_value}" if f.local_value else "",
                        f"TaxonWorks: {f.tw_value}" if f.tw_value else "") if x)
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{id_svc.catalog_label(af.catalog_number)} · {af.association}"
                                 f" · {_ANN_CATEGORY[f.category]}: {f.name}"
                                 + (f" — {vals}" if vals else "")).classes("text-xs")
                        ui.link("open in TaxonWorks", tw_compare.edit_url(af.tw_specimen_id),
                                new_tab=True).classes("text-xs")
        for count, what in (
                (assoc_result.not_on_tw_count,
                 "association(s) on specimens not on TaxonWorks yet — export them first"),
                (assoc_result.numberless_count,
                 "association(s) on specimens without a catalog number — never exported"),
                (assoc_result.taxon_subject_count,
                 "association(s) with a taxon as subject — not compared")):
            if count:
                ui.label(f"Not compared: {count} {what}.").classes("text-xs mt-1").style(soft)
        if assoc_result.ambiguous_catalog_numbers:
            ui.label("Not compared — catalog number held by more than one TaxonWorks "
                     "record: " + ", ".join(assoc_result.ambiguous_catalog_numbers)) \
                .classes("text-xs mt-1").style(soft)
        if assoc_result.unverified_terms:
            ui.label("Association tags/data attributes not compared — link not verified on "
                     "this instance: " + ", ".join(assoc_result.unverified_terms)) \
                .classes("text-xs mt-1").style(soft)
```

`_ANN_CATEGORY` maps the four annotation categories to titles; if its keys differ from `missing_on_tw` / `only_on_tw` / `value_differs` / `unlinked_on_tw`, read it at the top of the file and use the same keys `_render_annotation_results` uses.

- [ ] **Step 3: Run it in Compare**

Directly after the annotation-compare `try/except` block (the one that sets `ann_result`), add:

```python
                        _status(f"Checking {row['collection']} — comparing biological "
                                f"associations…")
                        assoc_result = None
                        assoc_error = None
                        try:
                            with session_factory() as s:
                                assoc_result = await tw_association_compare \
                                    .run_association_compare(s, repository_id=repo_id,
                                                             index=index)
                        except tw_svc.TaxonWorksUnreachable as assoc_exc:
                            assoc_error = str(assoc_exc)
```

and extend the stored result:

```python
                    state["compare"][repo_id] = {
                        "result": cmp_result, "checks": checks, "media": media_result,
                        "annotations": ann_result,
                        "associations": assoc_result, "associations_error": assoc_error,
                        "label": row["collection"],
                    }
```

Where the results render (`_render_annotation_results(data.get("annotations"))`), add after it:

```python
                        _render_association_results(data.get("associations"),
                                                    data.get("associations_error"))
```

The `if ann_result.associations_not_compared:` block is already gone (Task 2).

- [ ] **Step 4: Check the tab imports and the suite**

Run: `conda run -n collection python -c "import app.ui.tw_sync_tab"` — Expected: no error.
Run: `conda run -n collection python -m pytest -q` — Expected: all pass.

- [ ] **Step 5: Docs**

In `docs/taxonworks.md` §5c, after the media-compare material (before "### Verified API facts"), add:

```markdown
### Association compare (decided 2026-10-17)

Associations cannot be imported, so Compare lists their divergence for entry by hand
(`tw_association_compare.py`; design `docs/superpowers/specs/2026-10-17-association-compare-design.md`).
Anchored on specimens on TaxonWorks (catalog number); the relationship joins on its verified
`tw_id`; the other side is compared **by taxon name** (TaxonWorks OTU → taxon name; OTU ids
are never compared), with the name pre-flight's rules (subgenus-only = match with note;
differing authorship = differs; transliteration = note). An anatomical-part host is compared
by its OTU only (`/biological_associations/basic` gives `object_otu_id`; §5b). Groups: To add
on TaxonWorks / Only on TaxonWorks / Host differs (only for a single leftover pair) / Can't
compare. **A false "to add" is worse than no answer**: on a specimen with any Can't-compare
association its other unmatched ones are Can't compare too. Tags and data attributes on
matched associations are compared like a specimen's, and listed with every row to add.
Any lookup failure fails the whole section.
```

In `docs/app_structure.md`, in the service-layer module table, add a row after `tw_annotation_compare.py`'s (match the table's column format):

```markdown
| `tw_association_compare.py` | Read-only association compare (#149): TaxonWorks associations per specimen (`/basic` + plain, joined), host OTU → taxon name, matching and divergence groups, association tags / data attributes |
```

In `docs/superpowers/specs/2026-10-14-annotations-design.md`, replace the line that says biological-association annotations set `associations_not_compared = True` with: "Biological-association annotations are compared by the association compare (`docs/superpowers/specs/2026-10-17-association-compare-design.md`), on matched associations only."

- [ ] **Step 6: Commit**

```bash
git add app/ui/tw_sync_tab.py docs/taxonworks.md docs/app_structure.md \
        docs/superpowers/specs/2026-10-14-annotations-design.md
git commit -m "TaxonWorks tab: Biological associations section in Compare; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: UI check (controller, after asking the user)**

Ask the user before any database-related action. With a yes: a copy of the database on port 8091 (never 8080), the TaxonWorks tab → Compare against the user's TaxonWorks (read-only). Expect JJPC-00102's *collected from → rhizome: Typha L.* to be confirmed if the local association is *collected from → Typha L.*, otherwise listed. Screenshot each group; stop the server and delete the copy afterwards.
