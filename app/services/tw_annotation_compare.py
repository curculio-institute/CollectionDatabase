"""Annotation compare (spec 2026-10-14 "Compare") — read-only, diagnostic.

Annotation divergence cannot be repaired by exporting again (the importer is
CREATE-ONLY), so Compare is the only place it surfaces. Built like
``tw_media_compare.py``: fetch functions (I/O) + a pure ``compare_annotations`` +
``run_annotation_compare``. Reuses the catalog ``index`` and
``tw_media_compare.specimens_on_tw`` (ambiguous catalog numbers excluded, never guessed).

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* ``GET /api/v1/tags`` — filters ``tag_object_type``, ``tag_object_id[]``
  (``config/routes/api_v1.rb:179``; ``lib/queries/tag/filter.rb``); rows carry
  ``tag_object_type, tag_object_id, keyword_id, keyword.name``
  (``app/views/tags/api/v1/_attributes.json.jbuilder``).
* ``GET /api/v1/data_attributes`` — filters ``attribute_subject_type``,
  ``attribute_subject_id[]`` (``config/routes/api_v1.rb:151``;
  ``lib/queries/data_attribute/filter.rb``); rows carry ``type,
  controlled_vocabulary_term_id, predicate_name, import_predicate, value``
  (``app/views/data_attributes/api/v1/_attributes.json.jbuilder``). **No ORDER BY**
  (``data_attributes_controller.rb:50``) — ``fetch_all_pages`` de-duplicates. An
  ``ImportAttribute`` has no predicate id: reported as unlinked, by ``import_predicate``.
* ``GET /api/v1/collection_objects?collection_object_id[]=`` — the base filter's
  ``model_id_facet`` (``lib/queries/query/filter.rb:679``); rows carry
  ``collecting_event_id`` (``app/views/collection_objects/api/v1/_attributes.json.jbuilder:1``).
  Each imported row created its own TW event, so event annotations are compared PER
  SPECIMEN.

Only terms whose link is verified on the configured host are compared — a ``tw_id`` from
another instance names something else. Joins are on ``tw_id``, never on names.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

import app.services.taxonworks as tw
from app.models import CollectionObject
from app.services import annotations as ann
from app.services import tw_media_compare
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_compare import CatalogIndex

_TIMEOUT = httpx.Timeout(25.0)
_BATCH = 100        # ids per request — keeps the GET URL short
_PER_PAGE = 500


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT)


@dataclass(frozen=True)
class AnnotationFinding:
    catalog_number: str
    tw_object_id: int
    target: str                    # specimen | event
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
    associations_not_compared: bool = True   # stated in the report, like media_not_compared


@dataclass(frozen=True)
class TwTag:
    keyword_id: int
    name: str


@dataclass(frozen=True)
class TwDatum:
    predicate_id: int | None
    name: str
    value: str


def _batches(ids: list[int]):
    for i in range(0, len(ids), _BATCH):
        yield ids[i:i + _BATCH]


def _foreign(what: str, row_type, row_id) -> TaxonWorksUnreachable:
    return TaxonWorksUnreachable(
        f"{what} returned a row for {row_type} #{row_id} — the filter did not apply. "
        f"Treated as a lookup failure rather than as a set of matches.")


async def fetch_tags(object_type: str, ids: list[int]) -> dict[int, list[TwTag]]:
    out: dict[int, list[TwTag]] = defaultdict(list)
    if not ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(ids))):
            wanted = set(batch)
            what = f"the TaxonWorks tags of {len(batch)} {object_type} record(s)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if (row.get("tag_object_type") != object_type
                        or row.get("tag_object_id") not in wanted):
                    raise _foreign(what, row.get("tag_object_type"), row.get("tag_object_id"))
                if not isinstance(row.get("keyword_id"), int):
                    raise TaxonWorksUnreachable(f"{what} returned a tag without a keyword id.")

            rows = await tw.fetch_all_pages(
                client, "/tags",
                {"tag_object_type": object_type, "tag_object_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                name = str((r.get("keyword") or {}).get("name") or "").strip()
                out[r["tag_object_id"]].append(TwTag(r["keyword_id"], name))
    return dict(out)


async def fetch_data_attributes(object_type: str, ids: list[int]) -> dict[int, list[TwDatum]]:
    out: dict[int, list[TwDatum]] = defaultdict(list)
    if not ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(ids))):
            wanted = set(batch)
            what = f"the TaxonWorks data attributes of {len(batch)} {object_type} record(s)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if (row.get("attribute_subject_type") != object_type
                        or row.get("attribute_subject_id") not in wanted):
                    raise _foreign(what, row.get("attribute_subject_type"),
                                   row.get("attribute_subject_id"))
                if (row.get("type") != "ImportAttribute"
                        and not isinstance(row.get("controlled_vocabulary_term_id"), int)):
                    raise TaxonWorksUnreachable(
                        f"{what} returned a {row.get('type')} (#{row.get('id')}) without a "
                        f"predicate id.")
                if row.get("value") is None:
                    raise TaxonWorksUnreachable(
                        f"{what} returned data attribute #{row.get('id')} without a value.")

            rows = await tw.fetch_all_pages(
                client, "/data_attributes",
                {"attribute_subject_type": object_type, "attribute_subject_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                pid = r.get("controlled_vocabulary_term_id")
                if isinstance(pid, int):
                    name = str(r.get("predicate_name") or "").strip()
                else:
                    pid = None
                    name = f"{str(r.get('import_predicate') or '').strip()} (import attribute)"
                out[r["attribute_subject_id"]].append(
                    TwDatum(pid, name, str(r.get("value") or "").strip()))
    return dict(out)


async def fetch_event_ids(co_ids: list[int]) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    if not co_ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(co_ids))):
            wanted = set(batch)
            what = f"the TaxonWorks collection objects ({len(batch)} requested)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "CollectionObject", row.get("id"))
                if "collecting_event_id" not in row:
                    raise TaxonWorksUnreachable(
                        f"{what} returned CollectionObject #{row.get('id')} without a "
                        f"collecting_event_id key — cannot tell 'no event' from 'not sent'.")
                ev = row["collecting_event_id"]
                if ev is not None and (not isinstance(ev, int) or isinstance(ev, bool)):
                    raise TaxonWorksUnreachable(
                        f"{what} returned a non-integer collecting_event_id ({ev!r}) for "
                        f"CollectionObject #{row.get('id')}.")

            rows = await tw.fetch_all_pages(
                client, "/collection_objects", {"collection_object_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                ev = r.get("collecting_event_id")
                out[r["id"]] = ev
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return CollectionObject(s) {missing} that the "
                    f"identifier index lists — treated as a lookup failure.")
    return out


def _diff(findings: list, cat: str, tw_object_id: int, target: str, local: list,
          tw_tags: list[TwTag], tw_data: list[TwDatum], verified: dict) -> None:
    tag_by_twid = {t.tw_id: t for t in verified.values() if t.kind == "tag"}
    da_by_twid = {t.tw_id: t for t in verified.values() if t.kind == "data_attribute"}

    def add(kind, category, name, local_value=None, tw_value=None):
        findings.append(AnnotationFinding(cat, tw_object_id, target, kind, category, name,
                                          local_value, tw_value))

    local_tags = {a.term.tw_id: a.term for a in local
                  if a.term_kind == "tag" and a.term_id in verified}
    local_data = {a.term.tw_id: (a.term, a.value) for a in local
                  if a.term_kind == "data_attribute" and a.term_id in verified}

    tw_tag_ids = {t.keyword_id: t for t in tw_tags}
    for twid, term in local_tags.items():
        if twid not in tw_tag_ids:
            add("tag", "missing_on_tw", term.name)
    for twid, t in tw_tag_ids.items():
        if twid in local_tags:
            continue
        if twid in tag_by_twid:
            add("tag", "only_on_tw", tag_by_twid[twid].name)
        else:
            add("tag", "unlinked_on_tw", t.name)

    tw_values: dict[int, list[str]] = defaultdict(list)
    tw_names: dict[int, str] = {}
    for d in tw_data:
        if d.predicate_id is None:
            add("data_attribute", "unlinked_on_tw", d.name, tw_value=d.value)
            continue
        tw_values[d.predicate_id].append(d.value)
        tw_names.setdefault(d.predicate_id, d.name)
    for twid, (term, value) in local_data.items():
        values = tw_values.get(twid)
        if not values:
            add("data_attribute", "missing_on_tw", term.name, local_value=value)
        elif len(values) > 1 or values[0] != value:
            add("data_attribute", "value_differs", term.name, local_value=value,
                tw_value=" | ".join(sorted(values)))
    for twid, values in tw_values.items():
        if twid in local_data:
            continue
        joined = " | ".join(sorted(values))
        if twid in da_by_twid:
            add("data_attribute", "only_on_tw", da_by_twid[twid].name, tw_value=joined)
        else:
            add("data_attribute", "unlinked_on_tw", tw_names[twid], tw_value=joined)


def compare_annotations(session: Session, specimens: list[tuple[str, int, int]], *,
                        host: str, co_tags: dict, co_data: dict, event_of: dict,
                        ev_tags: dict, ev_data: dict,
                        ambiguous: tuple[str, ...] = ()) -> AnnotationCompareResult:
    """Pure computation, no I/O. ``specimens`` is ``(catalog_number, local co id, TW co
    id)`` from ``tw_media_compare.specimens_on_tw``."""
    terms = ann.list_terms(session)
    verified = {t.id: t for t in terms if t.tw_id is not None and t.tw_host == host}
    unverified = tuple(sorted({t.name for t in terms
                               if t.tw_id is not None and t.tw_host != host}))
    findings: list[AnnotationFinding] = []
    for cat, co_id, tw_id in specimens:
        co = session.get(CollectionObject, co_id)
        _diff(findings, cat, tw_id, "specimen", ann.annotations_for(session, "specimen", co_id),
              co_tags.get(tw_id, []), co_data.get(tw_id, []), verified)
        local_event = (ann.annotations_for(session, "event", co.collecting_event_id)
                       if co.collecting_event_id else [])
        tw_event = event_of[tw_id]
        _diff(findings, cat, tw_id, "event", local_event,
              ev_tags.get(tw_event, []) if tw_event else [],
              ev_data.get(tw_event, []) if tw_event else [], verified)
    return AnnotationCompareResult(
        checked_count=len(specimens), findings=tuple(findings), unverified_terms=unverified,
        ambiguous_catalog_numbers=tuple(ambiguous))


async def run_annotation_compare(session: Session, *, repository_id: int, index: CatalogIndex,
                                 host: str | None = None) -> AnnotationCompareResult:
    """I/O + computation — the entry point the TaxonWorks tab calls."""
    host = tw.configured_host() if host is None else host
    specimens, ambiguous = tw_media_compare.specimens_on_tw(
        session, repository_id=repository_id, index=index)
    tw_ids = [tw_id for _cat, _co, tw_id in specimens]
    co_tags = await fetch_tags("CollectionObject", tw_ids)
    co_data = await fetch_data_attributes("CollectionObject", tw_ids)
    event_of = await fetch_event_ids(tw_ids)
    ev_ids = sorted({e for e in event_of.values() if e})
    ev_tags = await fetch_tags("CollectingEvent", ev_ids)
    ev_data = await fetch_data_attributes("CollectingEvent", ev_ids)
    return compare_annotations(session, specimens, host=host, co_tags=co_tags,
                               co_data=co_data, event_of=event_of, ev_tags=ev_tags,
                               ev_data=ev_data, ambiguous=ambiguous)
