"""Annotation columns of the TaxonWorks occurrence export (spec "Export").

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* Headers ``TW:Tag:<Class>:<selector>`` and ``TW:DataAttribute:<Class>:<selector>`` with
  ``<Class>`` ∈ CollectionObject, CollectingEvent —
  ``app/models/dataset_record/darwin_core.rb:22`` (data attribute), ``:32`` (tag).
* Tag cell ``true`` / ``1`` applies, blank / ``false`` / ``0`` does not, anything else
  fails the row — ``occurrence.rb:1672``. Data attribute: blank cell skipped, otherwise an
  ``InternalAttribute`` with the cell as value — ``occurrence.rb:1695``.
* Event annotations are repeated on every row of the event. Safe only while rows never
  make TW reuse an existing event (we emit no ``eventID``): on reuse TW adds only new
  event TAGS and drops event DATA ATTRIBUTES silently (``occurrence.rb:469``).

A linked term means "this should reach TaxonWorks", so an unconfirmed link BLOCKS the
export (never a silent omission). Local-only terms are listed for information.
Association annotations are never exported (associations cannot be imported at all).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Annotation, CollectionObject

_PREFIX = {"tag": "Tag", "data_attribute": "DataAttribute"}
_CLASS_ORDER = {"CollectionObject": 0, "CollectingEvent": 1}
_KIND_ORDER = {"tag": 0, "data_attribute": 1}


@dataclass(frozen=True)
class ExportTerm:
    term_id: int
    kind: str           # tag | data_attribute
    tw_class: str       # CollectionObject | CollectingEvent
    tw_id: int
    tw_name: str
    local_name: str

    @property
    def header(self) -> str:
        return f"TW:{_PREFIX[self.kind]}:{self.tw_class}:{self.tw_name}"

    @property
    def link_kind(self) -> str:
        return "keyword" if self.kind == "tag" else "predicate"


@dataclass(frozen=True)
class AnnotationPlan:
    columns: tuple[ExportTerm, ...]
    cells: dict[int, dict[str, str]]          # collection_object.id -> {header: cell}
    blocked: tuple[tuple[str, str], ...]      # (local term name, reason)
    local_only: tuple[tuple[str, int], ...]   # (local term name, specimens carrying it)


def _unverified_reason(term, host: str) -> str:
    if not host:
        return "no TaxonWorks instance is configured, so its link cannot be confirmed"
    if term.tw_host is None:
        return (f"its TaxonWorks link is not verified on {host} — run Verify in "
                f"Controlled Vocabularies → Tags & data attributes")
    return (f"its TaxonWorks link was verified on {term.tw_host}, not on {host} — the id "
            f"may name something else here; run Verify again")


def annotation_columns(session: Session, cos: Iterable[CollectionObject], *,
                       host: str) -> AnnotationPlan:
    cos = list(cos)
    co_ids = [co.id for co in cos]
    event_of = {co.id: co.collecting_event_id for co in cos}
    event_ids = sorted({e for e in event_of.values() if e is not None})

    per_co: dict[int, list[tuple[str, Annotation]]] = defaultdict(list)
    if co_ids:
        for a in session.query(Annotation).filter(
                Annotation.collection_object_id.in_(co_ids)):
            per_co[a.collection_object_id].append(("CollectionObject", a))
    by_event: dict[int, list[Annotation]] = defaultdict(list)
    if event_ids:
        for a in session.query(Annotation).filter(
                Annotation.collecting_event_id.in_(event_ids)):
            by_event[a.collecting_event_id].append(a)
    for co in cos:
        for a in by_event.get(event_of[co.id], ()):
            per_co[co.id].append(("CollectingEvent", a))

    columns: dict[tuple[int, str], ExportTerm] = {}
    blocked: dict[str, str] = {}
    local: dict[str, set[int]] = defaultdict(set)
    for co_id, items in per_co.items():
        for tw_class, a in items:
            t = a.term
            if t.tw_id is None:
                local[t.name].add(co_id)
            elif not host or t.tw_host != host:
                blocked[t.name] = _unverified_reason(t, host)
            else:
                columns.setdefault((t.id, tw_class), ExportTerm(
                    term_id=t.id, kind=t.kind, tw_class=tw_class, tw_id=t.tw_id,
                    tw_name=t.tw_name, local_name=t.name))

    by_header: dict[str, list[ExportTerm]] = defaultdict(list)
    for c in columns.values():
        by_header[c.header].append(c)
    for header, same in by_header.items():
        if len(same) > 1:
            for c in same:
                blocked[c.local_name] = (f"shares the export column {header!r} with another "
                                         f"linked term")

    ordered = tuple(sorted(columns.values(), key=lambda c: (
        _CLASS_ORDER[c.tw_class], _KIND_ORDER[c.kind], c.tw_name.casefold())))
    cells: dict[int, dict[str, str]] = {}
    for co_id, items in per_co.items():
        row: dict[str, str] = {}
        for tw_class, a in items:
            c = columns.get((a.term_id, tw_class))
            if c is not None:
                row[c.header] = "true" if a.term_kind == "tag" else (a.value or "")
        cells[co_id] = row
    return AnnotationPlan(
        columns=ordered, cells=cells, blocked=tuple(sorted(blocked.items())),
        local_only=tuple(sorted((name, len(ids)) for name, ids in local.items())))
