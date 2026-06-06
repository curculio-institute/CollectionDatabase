"""Async TaxonWorks API client.

Only read-access (autocomplete + fetch). No writes — TW is a downstream mirror.
Verified endpoint shapes against sfg.taxonworks.org @2026-06-04.

Connection settings (base URL, token, TaxonPages URL) are read from AppConfig on
every call so changes made in the settings dialog take effect without a restart.
"""
from __future__ import annotations

import asyncio

import httpx

from app.config import get_config

_TIMEOUT = httpx.Timeout(6.0)


def _base() -> str:
    return get_config().tw_base.rstrip("/")


def _token() -> str:
    return get_config().tw_token


def taxonpages_url(otu_id: int) -> str:
    return f"{get_config().taxonpages_base.rstrip('/')}/#/otus/{otu_id}"


async def search_taxon_names(term: str, limit: int = 20) -> list[dict]:
    """Autocomplete — returns list of {id, name, label, label_html, valid_taxon_name_id}."""
    if len(term.strip()) < 2:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/taxon_names/autocomplete",
            params={"term": term.strip(), "project_token": _token()},
        )
        r.raise_for_status()
        return r.json()[:limit]


async def fetch_taxon_name(tw_id: int) -> dict | None:
    """Full taxon_name record: name, rank, cached, cached_author_year, parent_id, …"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/taxon_names/{tw_id}",
            params={"project_token": _token()},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


# Ranks we want to collect while walking up the parent chain.
# Maps TW rank string → local Taxon attribute name.
_ANCESTOR_RANKS: dict[str, str] = {
    "order":       "taxon_order",    # "taxon_" prefix avoids confusion with SQL ORDER keyword
    "suborder":    "suborder",       # was wrongly aliased to "taxon_order" — now separate
    "superfamily": "superfamily",
    "family":      "family",
    "subfamily":   "subfamily",
    "tribe":       "tribe",
    "subtribe":    "subtribe",
    "genus":       "genus",
    "subgenus":    "subgenus",
    "species":     "specific_epithet",  # needed for subspecies/variety/form name building
}
# Stop climbing once we hit one of these — nothing above is useful for local rows.
# "division" is the ICN equivalent of "phylum" (plants/algae/fungi in TaxonWorks).
_STOP_RANKS = {"order", "class", "phylum", "division", "kingdom", "subphylum", "superorder"}


async def fetch_full_classification(tw_id: int, _depth: int = 0) -> dict | None:
    """Return the target taxon_name record augmented with ancestor classification
    fields (family, subfamily, tribe, subtribe, genus, subgenus, taxon_order).

    Walks parent_id links sequentially until a stop-rank is reached.
    Fields are added as top-level keys so _fields_from_tw can read them directly,
    e.g. record['family'] = 'Curculionidae'.

    Synonym detection: if cached_is_valid is False, also fetches the valid name's
    full classification and attaches it as '_valid_tw_data' / '_valid_otu_id', so
    get_or_create_from_tw_data can create the accepted taxon and link the synonym.
    _depth guards against synonym chains (only one level of recursion).
    """
    record = await fetch_taxon_name(tw_id)
    if record is None:
        return None

    augmented = dict(record)
    parent_id = record.get("parent_id")
    seen: set[int] = {tw_id}
    ancestor_tw_ids: dict[str, int] = {}  # field key → TW taxon_name id

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            r = await client.get(
                f"{_base()}/taxon_names/{parent_id}",
                params={"project_token": _token()},
            )
            if r.status_code == 404:
                break
            r.raise_for_status()
            parent = r.json()

            p_rank = (parent.get("rank") or "").lower()
            p_name = parent.get("name") or ""
            field  = _ANCESTOR_RANKS.get(p_rank)

            if field and p_name:
                if field not in augmented:
                    augmented[field] = p_name
                    ancestor_tw_ids[field] = parent_id  # record TW id for OTU lookup
                p_auth = parent.get("cached_author_year") or parent.get("cached_author")
                if p_auth:
                    augmented.setdefault(f"{field}_authorship", p_auth)

            if p_rank in _STOP_RANKS:
                break

            parent_id = parent.get("parent_id")

    # Fetch OTU IDs for all collected ancestors concurrently.
    if ancestor_tw_ids:
        fields_list = list(ancestor_tw_ids.keys())
        otu_results = await asyncio.gather(
            *[fetch_otu_id_for_taxon_name(tid) for tid in ancestor_tw_ids.values()],
            return_exceptions=True,
        )
        for field, otu_id in zip(fields_list, otu_results):
            if isinstance(otu_id, int):
                augmented[f"{field}_otu_id"] = otu_id

    # For subspecies/variety/form: build the full species name from genus + epithet
    # so _ensure_parent_rows can create the species row as the immediate parent.
    if "specific_epithet" in augmented and "genus" in augmented:
        augmented.setdefault(
            "species_name",
            f"{augmented['genus']} {augmented['specific_epithet']}",
        )
        if "specific_epithet_otu_id" in augmented:
            augmented["species_name_otu_id"] = augmented["specific_epithet_otu_id"]

    # Synonym detection — verified via cached_is_valid / cached_valid_taxon_name_id
    # (taxon_names API, e.g. /api/v1/taxon_names/824298).
    if not record.get("cached_is_valid", True) and _depth == 0:
        valid_id = record.get("cached_valid_taxon_name_id")
        if valid_id and valid_id != tw_id:
            valid_data, valid_otu = await asyncio.gather(
                fetch_full_classification(valid_id, _depth=1),
                fetch_otu_id_for_taxon_name(valid_id),
            )
            if valid_data:
                augmented["_valid_tw_data"] = valid_data
                augmented["_valid_otu_id"]  = valid_otu

    return augmented


async def fetch_biological_relationships() -> list[dict]:
    """Return all BiologicalRelationship records from TW for this project.

    Each record: {id, name, definition, inverted_name, …}
    Used by sync_biological_relationships() at session start.
    Verified endpoint: GET /api/v1/biological_relationships (no show endpoint).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/biological_relationships",
            params={"project_token": _token(), "per": 500},
        )
        r.raise_for_status()
        return r.json()


async def fetch_otu_id_for_taxon_name(taxon_name_id: int) -> int | None:
    """Return the OTU id associated with a taxon_name_id, or None if not found.
    Used to build TaxonPages deep-link URLs."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_base()}/otus",
            params={"taxon_name_id[]": taxon_name_id, "project_token": _token()},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]["id"]
        return None
