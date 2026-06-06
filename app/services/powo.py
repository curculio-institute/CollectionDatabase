"""POWO (Plants of the World Online) / IPNI search client.

Fallback taxon source for plant/fungus names not found in TaxonWorks.
Search order in the bio-association widget: local DB → TaxonWorks → POWO.

IPNI autocomplete → parallel POWO batch fetch → local Taxon row creation.
POWO records provide synonym status, the accepted-name link, and nomenclaturalCode.
IPNI search results do not contain taxonomicStatus (always null); we must fetch
the full POWO record to know whether a name is a synonym.
"""
from __future__ import annotations

import asyncio

import httpx

_IPNI_SEARCH = "https://www.ipni.org/api/1/search"
_POWO_TAXON  = "https://powo.science.kew.org/api/2/taxon"
_TIMEOUT     = httpx.Timeout(8.0)

# POWO nomenclaturalCode values → DwC equivalents.
_CODE_MAP: dict[str, str] = {
    "botanical":      "ICN",
    "icn":            "ICN",
    "algological":    "ICN",
    "bacteriological": "ICNP",
    "viral":          "ICVCN",
    "zoological":     "ICZN",
    "iczn":           "ICZN",
}


def map_powo_code(raw: str) -> str | None:
    """Map a POWO/IPNI nomenclaturalCode string to the DwC uppercase form."""
    return _CODE_MAP.get((raw or "").lower().strip())


async def search_ipni(term: str, limit: int = 10) -> list[dict]:
    """Autocomplete plant/fungus names via IPNI; filter to taxa present in POWO.

    Returns raw IPNI result dicts (no taxonomicStatus — always null from IPNI).
    Use search_powo() when synonym status is needed.
    """
    if len(term.strip()) < 2:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            _IPNI_SEARCH,
            params={"q": term.strip(), "perPage": limit * 2},
        )
        r.raise_for_status()
        data = r.json()
    results = [rec for rec in data.get("results", []) if rec.get("inPowo")]
    return results[:limit]


async def fetch_powo_taxon(ipni_id: str) -> dict | None:
    """Fetch a full POWO taxon record by IPNI ID.

    Accepts either the numeric form ("320035-2") or the full URN.
    Returns None on 404.

    Key response fields:
      name, authors, rank, family, genus,
      nomenclaturalCode, taxonomicStatus, synonym (bool),
      accepted: {fqId, name, author} — present when synonym=True
    """
    fq_id = (
        ipni_id if ipni_id.startswith("urn:")
        else f"urn:lsid:ipni.org:names:{ipni_id}"
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_POWO_TAXON}/{fq_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def search_powo(term: str, limit: int = 8) -> list[dict]:
    """IPNI autocomplete + parallel POWO batch fetch.

    Returns full POWO taxon records (merged with IPNI fields) so the
    caller has synonym status, accepted-name links, and nomenclaturalCode
    without any further API calls.

    POWO is only fetched when TW returns nothing, so the extra N parallel
    requests are acceptable.
    """
    ipni_results = await search_ipni(term, limit=limit)
    if not ipni_results:
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async def _fetch_one(r: dict) -> dict:
            fq_id = r.get("fqId", "")
            if not fq_id:
                return r
            try:
                resp = await client.get(f"{_POWO_TAXON}/{fq_id}")
                if resp.status_code == 404:
                    return r
                resp.raise_for_status()
                powo = resp.json()
                # Merge: POWO fields win over IPNI on conflicts (POWO is richer).
                return {**r, **powo}
            except Exception:
                return r  # fall back to IPNI-only data; synonym status unknown

        records = await asyncio.gather(*[_fetch_one(r) for r in ipni_results])

    return list(records)


def fields_from_powo(powo: dict) -> dict:
    """Extract Taxon field values from a full POWO taxon record.

    Returns a flat dict suitable for get_or_create_from_powo_data():
      scientific_name, taxon_rank, scientific_name_authorship,
      nomenclatural_code, family, genus,
      is_synonym (bool), accepted_fqid, accepted_name, accepted_authorship
    """
    rank = (powo.get("rank") or "").lower()
    name = powo.get("name") or ""
    auth = powo.get("authors") or None
    code = map_powo_code(powo.get("nomenclaturalCode") or "")

    # classification array gives family → genus → species (no tribe/subfamily
    # available from POWO — confirmed by API exploration on 2026-06-06).
    family = powo.get("family") or None
    genus  = powo.get("genus")  or None

    is_synonym = bool(powo.get("synonym", False))

    accepted_raw  = powo.get("accepted") or {}
    accepted_fqid = accepted_raw.get("fqId") or None
    accepted_name = accepted_raw.get("name") or None
    accepted_auth = accepted_raw.get("author") or None

    return {
        "scientific_name":             name,
        "taxon_rank":                  rank,
        "scientific_name_authorship":  auth,
        "nomenclatural_code":          code,
        "family":                      family,
        "genus":                       genus,
        "is_synonym":                  is_synonym,
        "accepted_fqid":               accepted_fqid,
        "accepted_name":               accepted_name,
        "accepted_authorship":         accepted_auth,
    }
