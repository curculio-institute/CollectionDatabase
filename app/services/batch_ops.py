"""Batch tools — build a collection-scoped specimen set and apply a bulk operation
to it: reassign collection or set disposition (#78, parent #72).

Two ways to build the set (both **collection-scoped**):
  * by taxon — every specimen of a taxon (and its descendants) *in the working
    collection*;
  * by a pasted catalog-number list — matched against the working collection.

Then one optional bulk op over the matched set: set ``disposition_id`` or reassign
``repository_id`` (give specimens away). ``catalog_number`` is never touched — only the FK.

SAFETY (hard requirement, #78 discussion): every fetch and every apply is scoped to a
single *working collection* (a ``repository_id``). The taxon fetch and the paste matcher
both filter on ``repository_id``; ``_load_in_scope`` re-asserts it before any write. So a
specimen belonging to another collection can **never** be listed or modified — you only
move / re-dispose specimens you physically hold. Bad input is reported loudly, never
silently skipped (project rule).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import CollectionObject, Taxon, TaxonDetermination
from app.models.base import _utcnow
from app.services.taxa import format_scientific_name


# ── data shapes ─────────────────────────────────────────────────────────────

@dataclass
class SpecimenMatch:
    """An in-scope specimen, safe to operate on."""
    co_id: int
    catalog: str
    taxon_label: str
    disposition: str        # current disposition name, or "" if none


@dataclass
class ForeignHit:
    """A pasted catalog number that exists, but in another collection → excluded."""
    catalog: str
    collection_code: str


@dataclass
class MatchResult:
    matched: list[SpecimenMatch] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)   # no specimen in any collection
    foreign: list[ForeignHit] = field(default_factory=list)  # in another collection


# ── helpers ─────────────────────────────────────────────────────────────────

def _taxon_index(session: Session) -> dict[int, Taxon]:
    return {t.id: t for t in session.query(Taxon).all()}


def descendant_taxon_ids(session: Session, taxon_id: int) -> set[int]:
    """``taxon_id`` plus every taxon under it in the parent-link tree.

    Picking a species returns just it (+ its subspecies); picking a genus returns all
    its species. Mirrors Explore's descendant expansion, computed from the parent links.
    """
    children: dict[int, list[int]] = defaultdict(list)
    for tid, pid in session.query(Taxon.id, Taxon.parent_name_usage_id).all():
        if pid is not None:
            children[pid].append(tid)
    out: set[int] = set()
    stack = [taxon_id]
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        stack.extend(children[x])
    return out


def _current_taxon_label(co: CollectionObject, idx: dict[int, Taxon]) -> str:
    for d in co.determinations:
        if d.is_current:
            t = idx.get(d.taxon_id)
            return format_scientific_name(t) if t else "— undetermined —"
    return "— undetermined —"


def _to_match(co: CollectionObject, idx: dict[int, Taxon]) -> SpecimenMatch:
    return SpecimenMatch(
        co_id=co.id,
        catalog=co.catalog_number,
        taxon_label=_current_taxon_label(co, idx),
        disposition=(co.disposition.name if co.disposition else ""),
    )


# ── build the specimen set ───────────────────────────────────────────────────

def fetch_by_taxon(
    session: Session, *, repository_id: int, taxon_id: int
) -> list[SpecimenMatch]:
    """Every specimen of ``taxon_id`` (and descendants) **in the working collection**.

    Scoped to ``repository_id`` so specimens of the same taxon held in another
    collection are never returned.
    """
    ids = descendant_taxon_ids(session, taxon_id)
    idx = _taxon_index(session)
    q = (
        session.query(CollectionObject)
        .join(
            TaxonDetermination,
            (TaxonDetermination.collection_object_id == CollectionObject.id)
            & (TaxonDetermination.is_current == 1),
        )
        .filter(CollectionObject.repository_id == repository_id)
        .filter(TaxonDetermination.taxon_id.in_(ids))
        .order_by(CollectionObject.catalog_number)
    )
    return [_to_match(co, idx) for co in q.all()]


def parse_catalog_numbers(text: str) -> list[str]:
    """Split a pasted blob into a de-duped, order-preserving catalog-number list.

    Separators: newlines, commas, semicolons, or whitespace. Blank tokens dropped.
    """
    tokens = [t.strip() for t in re.split(r"[\s,;]+", text or "")]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def match_catalog_numbers(
    session: Session, *, repository_id: int, numbers: list[str]
) -> MatchResult:
    """Classify each catalog number against the working collection.

    * **matched** — a specimen with this catalog number exists in the working
      collection (safe to operate on);
    * **foreign** — it exists only in another collection → excluded (never modified);
    * **not_found** — no specimen anywhere with this catalog number.
    """
    idx = _taxon_index(session)
    result = MatchResult()
    for cat in numbers:
        rows = (
            session.query(CollectionObject)
            .options()
            .filter(CollectionObject.catalog_number == cat)
            .all()
        )
        if not rows:
            result.not_found.append(cat)
            continue
        here = next((co for co in rows if co.repository_id == repository_id), None)
        if here is not None:
            result.matched.append(_to_match(here, idx))
        else:
            other = rows[0]
            result.foreign.append(
                ForeignHit(catalog=cat, collection_code=other.repository.collection_code)
            )
    return result


# ── apply a bulk operation (in-scope only) ───────────────────────────────────

def _load_in_scope(
    session: Session, source_repository_id: int, co_ids: list[int]
) -> list[CollectionObject]:
    """Load the specimens and REFUSE if any is missing or not in the working collection.

    This is the last-line guarantee that a bulk op never touches a cross-collection
    specimen, independent of how the caller built the id list.
    """
    ids = list(dict.fromkeys(co_ids))  # de-dupe, keep order
    cos = session.query(CollectionObject).filter(CollectionObject.id.in_(ids)).all()
    found = {co.id for co in cos}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValueError(f"Specimen ids not found: {missing}")
    foreign = [co.id for co in cos if co.repository_id != source_repository_id]
    if foreign:
        raise ValueError(
            f"Refusing to modify {len(foreign)} specimen(s) that are not in the working "
            f"collection (cross-collection safety): {foreign}"
        )
    return cos


def apply_disposition(
    session: Session, *, source_repository_id: int, co_ids: list[int],
    disposition_id: int | None,
) -> int:
    """Set ``disposition_id`` on every in-scope specimen. Returns the count updated."""
    cos = _load_in_scope(session, source_repository_id, co_ids)
    now = _utcnow()
    for co in cos:
        co.disposition_id = disposition_id
        co.updated_at = now
    session.flush()
    return len(cos)


def apply_repository(
    session: Session, *, source_repository_id: int, co_ids: list[int],
    target_repository_id: int,
) -> int:
    """Reassign every in-scope specimen to ``target_repository_id`` (give them away).

    ``catalog_number`` is unchanged (it stays the immutable identifier, prefix and all);
    only the owning-collection FK moves. Returns the count updated.
    """
    if not target_repository_id:
        raise ValueError("A target collection is required.")
    if target_repository_id == source_repository_id:
        raise ValueError("Target collection is the same as the working collection.")
    cos = _load_in_scope(session, source_repository_id, co_ids)
    now = _utcnow()
    for co in cos:
        co.repository_id = target_repository_id
        co.updated_at = now
    session.flush()
    return len(cos)
