"""Explore favorites — saved searches (#137).

A favorite is a named snapshot of the Explore search state: the stacked-group structure
``[{op, facets:[{kind,key,label,tag}]}]`` stored as JSON on ``saved_search.payload``.
Because a facet key references a DB entity (taxon_id, geo-vocab id, repository_id, person
full_name), a favorite is re-*resolved* against the live DB on load — a key that no longer
resolves is flagged ``stale`` and shown greyed, never silently applied (CLAUDE.md §2).

Storage rule (same as person defaults / repository default): a configurable default that
references DB entities lives in the DB, not config.json.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import (
    SavedSearch, Taxon, Person, Repository,
    Country, StateProvince, County, Island, AdministrativeRegion,
)
from app.models.base import _utcnow
from app.services.taxa import format_scientific_name

# facet kind → (vocab model) for the geography facets (label is the row's name).
_GEO_MODELS = {
    "country": Country, "state_province": StateProvince,
    "administrative_region": AdministrativeRegion, "county": County, "island": Island,
}


# ── CRUD ──────────────────────────────────────────────────────────────────────

def list_searches(session: Session) -> list[SavedSearch]:
    """All favorites, in user order (sort_order, then id)."""
    return (session.query(SavedSearch)
            .order_by(SavedSearch.sort_order, SavedSearch.id).all())


def get_default(session: Session) -> SavedSearch | None:
    """The favorite auto-applied when Explore opens, or None."""
    return session.query(SavedSearch).filter(SavedSearch.is_default == 1).first()


def create(session: Session, name: str, groups: list[dict]) -> SavedSearch:
    """Save the current search ``groups`` under ``name`` (unique, non-empty)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A favorite needs a name.")
    if session.query(SavedSearch).filter(SavedSearch.name == name).first():
        raise ValueError(f"A favorite named “{name}” already exists.")
    if not any(g.get("facets") for g in groups):
        raise ValueError("Nothing to save — add at least one filter first.")
    last = (session.query(SavedSearch)
            .order_by(SavedSearch.sort_order.desc()).first())
    now = _utcnow()
    fav = SavedSearch(name=name, payload=json.dumps(groups),
                      sort_order=((last.sort_order + 1) if last else 0),
                      created_at=now, updated_at=now)
    session.add(fav)
    session.flush()
    return fav


def rename(session: Session, search_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ValueError("A favorite needs a name.")
    clash = (session.query(SavedSearch)
             .filter(SavedSearch.name == name, SavedSearch.id != search_id).first())
    if clash:
        raise ValueError(f"A favorite named “{name}” already exists.")
    fav = session.get(SavedSearch, search_id)
    if fav is None:
        raise ValueError(f"Saved search {search_id} not found")
    fav.name = name
    fav.updated_at = _utcnow()
    session.flush()


def delete(session: Session, search_id: int) -> None:
    fav = session.get(SavedSearch, search_id)
    if fav is not None:
        session.delete(fav)
        session.flush()


def set_default(session: Session, search_id: int | None) -> None:
    """Make ``search_id`` the sole default (auto-applied on open), or clear the default
    entirely when ``None``. Clears the old default first so the partial-unique index
    never trips mid-statement (mirrors repository.set_default)."""
    now = _utcnow()
    session.query(SavedSearch).filter(SavedSearch.is_default == 1).update(
        {"is_default": 0, "updated_at": now})
    if search_id is not None:
        if session.get(SavedSearch, search_id) is None:
            raise ValueError(f"Saved search {search_id} not found")
        session.query(SavedSearch).filter(SavedSearch.id == search_id).update(
            {"is_default": 1, "updated_at": now})
    session.flush()


def reorder(session: Session, ordered_ids: list[int]) -> None:
    """Set sort_order to match the given id order."""
    now = _utcnow()
    for pos, sid in enumerate(ordered_ids):
        session.query(SavedSearch).filter(SavedSearch.id == sid).update(
            {"sort_order": pos, "updated_at": now})
    session.flush()


# ── resolution (staleness) ──────────────────────────────────────────────────────

def _resolve_facet(session: Session, kind: str, key) -> str | None:
    """Current display label for a facet, or None if its target no longer exists."""
    if kind == "taxon":
        t = session.get(Taxon, int(key))
        return format_scientific_name(t) if t else None
    if kind in _GEO_MODELS:
        v = session.get(_GEO_MODELS[kind], int(key))
        return v.name if v else None
    if kind in ("collector", "identified_by"):
        # key is a person full_name (UNIQUE); it is stale if no such person remains.
        p = session.query(Person).filter(Person.full_name == key).first()
        return p.full_name if p else None
    if kind == "collection":
        r = session.get(Repository, int(key))
        if not r:
            return None
        return (f"{r.collection_code} — {r.collection_full_name}"
                if r.collection_full_name else r.collection_code)
    return None


def resolve(session: Session, fav: SavedSearch) -> dict:
    """Re-resolve a favorite's payload against the live DB.

    Returns ``{"groups": [...], "stale": int}`` where each facet carries a refreshed
    ``label`` and a ``stale`` flag; ``stale`` is the count of facets whose target is gone.
    The label is refreshed so a renamed taxon shows its current name, not the saved one.
    """
    groups = json.loads(fav.payload)
    stale = 0
    for g in groups:
        for f in g.get("facets", []):
            label = _resolve_facet(session, f["kind"], f["key"])
            if label is None:
                f["stale"] = True
                stale += 1
            else:
                f["stale"] = False
                f["label"] = label
    return {"groups": groups, "stale": stale}


def apply_groups(resolved_groups: list[dict]) -> list[dict]:
    """Strip a resolved favorite down to what can actually be applied: drop stale facets,
    then drop any group left empty. Returns clean ``groups`` for the Explore state."""
    out = []
    for g in resolved_groups:
        facets = [{"kind": f["kind"], "key": f["key"], "label": f["label"], "tag": f["tag"]}
                  for f in g.get("facets", []) if not f.get("stale")]
        if facets:
            out.append({"op": g.get("op", "and"), "facets": facets})
    return out
