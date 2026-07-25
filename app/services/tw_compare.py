"""Step 1 of #149 — Compare local vs. TaxonWorks, read-only.

The issue's own wording ("Step 1: Compare"):

    1. Check which records (specimens) are in the local database.
    2. Determine specimens eligible for export (confidential specimen/event/recorder).
    3. Check which specimens are on TaxonWorks — catalogNumber determines identity;
       report a catalogNumber that appears twice.
    4. Determine specimens on TaxonWorks that should not be there (confidential locally).
    5. Determine differences between local and TaxonWorks; link straight to the editor.
    6. Also compare associated media.

Points 1–2 are already `dwc_export.export_decision` / a `CollectionObject` query — this
module adds only the part that needs the network: point 3 (existence + duplicates) and
point 5 (field-level diff), read via `GET /api/v1/dwc_occurrences`. Point 6 (media) is
**not implemented** — TW's projection carries at most one `associatedMedia` URL per row
with no shared key to our `media_attachment` rows, and guessing a match would be exactly
the "silent wrong value" CLAUDE.md §2 rules out; `CompareResult.media_not_compared` says so
plainly rather than reporting a false "0 differences".

**Verified against the sandbox project 2026-07-25** (read-only GETs, not guessed):
- `/dwc_occurrences` rows are sparse — a field key is present only when TaxonWorks
  actually holds that value (a specimen with no bound identifier has no `catalogNumber`
  key at all, not an empty string).
- `catalogNumber` / `institutionCode` (when present) match our own values verbatim — no
  namespace transform happens on import.
- `occurrenceID` on the TW side is a TaxonWorks-generated UUID, **not** the string we
  wrote (`institutionCode:collectionCode:catalogNumber`) — CLAUDE.md §5 already says why
  (`occurrenceID` becomes `Identifier::Local::Import::Dwc`, a separate identifier record).
  Comparing it would report every single row as "diverged"; it is excluded from the diff.
- **`catalogNumber=` DOES filter, exactly** (re-verified after an earlier draft of this
  module wrongly assumed it was silently ignored, like `taxon_names`' scalar `name=`).
  Confirmed by reading the TaxonWorks source itself
  (`app/controllers/dwc_occurrences_controller.rb` → `Queries::DwcOccurrence::Filter` →
  `lib/queries/concerns/attributes.rb`): every real `DwcOccurrence` column, camelCase DwC
  term included, is a permitted exact-match query param via a generic "loop every
  ATTRIBUTES column, `table[a].eq(params[a])`" mechanism — `catalogNumber` is one such
  column. Measured: `?catalogNumber=JJPC-00001` returns exactly the one matching row in
  0.57s, against 79s for an unfiltered page-through. So this module fetches **one request
  per local catalog number**, bounded concurrency (`_MAX_CONCURRENCY`, mirrors
  `tw_sync.py`'s identical discipline for the same reason: a shared public server, never a
  burst) — 39 real specimens completed in ~2.5–3s, verified. A duplicate catalogNumber on
  TW is still caught correctly: the exact filter returns *every* row sharing that string,
  not just one, so `len(rows) > 1` for one lookup is exactly #149 step 1.3's case.

Reuses `dwc_export.occurrence_row` for the local side of the diff — the identical
projection a real export would write, so "diverged" can never disagree with what the
spreadsheet actually contains. Does not import from `tw_sync.py` or modify either of
those two files; the only private surface reused across a service boundary is
`taxonworks.TaxonWorksUnreachable` (a public exception class, the designated contract for
"could not reach/authenticate against TaxonWorks" — everything else here goes through
`get_config()` and its own small `httpx` calls rather than reaching into that module's
underscore-prefixed helpers).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import CollectionObject
from app.services import dwc_export
from app.services.taxonworks import TaxonWorksUnreachable, web_base

_TIMEOUT = httpx.Timeout(25.0)
# A shared public server — bound concurrency so a check run never fires a burst of
# requests at it (mirrors tw_sync.py's identical `_MAX_CONCURRENCY`, same reasoning,
# same value — one convention, not two).
_MAX_CONCURRENCY = 4
# A single request is small (one exact-match lookup) so a slow one is a genuine blip,
# not the endpoint being fundamentally heavy — retried rather than failing the whole
# batch over one hiccup. Mirrors the Overpass discipline (CLAUDE.md): short backoff,
# bounded attempts, never silently give up on a transient failure.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF = (0.5, 1.5)

# Fields compared between our local occurrence_row projection and TaxonWorks' own —
# deliberately NOT all of dwc_export.DWC_COLUMNS: occurrenceID/institutionCode/
# collectionCode are namespace bookkeeping that TW rewrites on import (measured above),
# comparing them would flag every row as diverged for no informative reason. The issue
# calls out "of particular interest are identifications and repositories" — scientificName
# (+ authorship) is the identification; institutionCode is compared separately (below,
# report-only) rather than as a diff field, for the same rewriting reason.
_DIFF_FIELDS: tuple[str, ...] = (
    "basisOfRecord", "individualCount", "sex", "preparations", "typeStatus",
    "recordedBy", "eventDate", "verbatimEventDate", "country", "stateProvince",
    "verbatimLocality", "scientificName", "scientificNameAuthorship", "taxonRank",
    "identificationQualifier", "identifiedBy",
)


def _base() -> str:
    return get_config().tw_base.rstrip("/")


def _explain(exc: Exception) -> TaxonWorksUnreachable:
    """Same shape of message as `taxonworks._explain` (host/status/timeout), kept local
    rather than importing that private helper — see module docstring."""
    host = urlsplit(_base()).netloc or _base()
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return TaxonWorksUnreachable(
                f"{host} rejected the project token ({code}) — check Settings → "
                f"TaxonWorks connection."
            )
        return TaxonWorksUnreachable(f"{host} answered {code}.")
    if isinstance(exc, httpx.TimeoutException):
        return TaxonWorksUnreachable(f"{host} did not answer in time.")
    return TaxonWorksUnreachable(f"Cannot reach {host} ({type(exc).__name__}).")


async def _fetch_one_catalog_number(
    client: httpx.AsyncClient, token: str, catalog_number: str
) -> list[dict]:
    """Every `dwc_occurrences` row whose `catalogNumber` exactly equals
    `catalog_number` — normally 0 or 1, more than 1 is a real duplicate on TW (#149
    step 1.3). Retries a transient failure (`_MAX_ATTEMPTS`, short backoff) before
    raising — one slow/failed lookup among many must not be indistinguishable from
    "not on TaxonWorks", which would be exactly the silent wrong value CLAUDE.md §2
    rules out.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = await client.get(
                f"{_base()}/dwc_occurrences",
                params={"catalogNumber": catalog_number, "per": 10,
                        "project_token": token},
            )
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504):
                last_exc = exc
            else:
                raise _explain(exc) from exc
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
    raise _explain(last_exc) from last_exc


async def fetch_tw_rows_for_catalog_numbers(
    catalog_numbers: list[str], on_progress=None
) -> dict[str, list[dict]]:
    """`{catalog_number: [dwc_occurrences row, ...]}` for exactly the catalog numbers
    given — one exact-match request per number (module docstring: verified, not the
    unfiltered full-mirror crawl an earlier draft used), bounded concurrency so a check
    run never bursts the server. `on_progress(done, total)` fires as each lookup
    completes, so the UI shows live progress rather than a long silent wait (the same
    "each source writes its own field the moment it lands" discipline CLAUDE.md
    documents for the geocoder) — measured ~3s for 39 real specimens, vs. 79s for the
    old full-crawl approach against the same project.
    """
    cfg = get_config()
    if not cfg.tw_base.strip() or not cfg.tw_token.strip():
        raise TaxonWorksUnreachable(
            "TaxonWorks is not configured — Settings → TaxonWorks connection."
        )
    result: dict[str, list[dict]] = {}
    done = 0
    total = len(catalog_numbers)
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _one(client: httpx.AsyncClient, cat: str) -> None:
        nonlocal done
        async with sem:
            rows = await _fetch_one_catalog_number(client, cfg.tw_token, cat)
        result[cat] = [
            row for row in rows
            if row.get("dwc_occurrence_object_type") == "CollectionObject"
        ]
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        await asyncio.gather(*[_one(client, cat) for cat in catalog_numbers])
    return result


def edit_url(dwc_occurrence_object_id: int) -> str:
    """The comprehensive specimen editor deep link, exact schema from #149's own text:
    ``{web root}/tasks/accessions/comprehensive?collection_object_id={id}``."""
    return f"{web_base()}/tasks/accessions/comprehensive?collection_object_id={dwc_occurrence_object_id}"


@dataclass(frozen=True)
class DivergedRow:
    catalog_number: str
    tw_object_id: int
    field_diffs: tuple[str, ...]   # "fieldName: local='x' vs TW='y'"


@dataclass(frozen=True)
class DuplicateGroup:
    catalog_number: str
    tw_object_ids: tuple[int, ...]
    institution_codes: tuple[str, ...]   # parallel to tw_object_ids, "" where absent


@dataclass(frozen=True)
class LeakedRow:
    """An eligible-to-be-withheld specimen (confidential) that TaxonWorks still has."""
    catalog_number: str
    tw_object_id: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CompareResult:
    checked_count: int              # local specimens compared (eligible + ineligible)
    eligible_count: int
    ineligible_count: int
    synced_count: int                # eligible, on TW, AND no field differs — the
                                      # "correctly synced" count the report table shows
    not_on_tw: tuple[str, ...]      # eligible locally, catalogNumber not found on TW
    diverged: tuple[DivergedRow, ...]
    duplicates: tuple[DuplicateGroup, ...]
    leaked: tuple[LeakedRow, ...]
    media_not_compared: bool = True


def _diff_one(session: Session, co: CollectionObject, tw_row: dict) -> tuple[str, ...]:
    """Field-by-field diff, case-insensitive on purpose.

    Measured against the live sandbox project (2026-07-25): TaxonWorks re-cases enum-like
    values on its side — every `preparations` "pinned" we sent came back "Pinned", every
    `sex` "female" came back "Female" — apparently how it labels its biocuration classes,
    not a data disagreement. A case-sensitive compare flagged ~20 of 28 "diverged" rows on
    that alone, all differing in *nothing* but capitalisation, drowning out the handful
    that were real (a genuinely different `scientificName`, a dropped
    `identificationQualifier`). Comparing on `casefold()` — still *reporting* the values
    in their original case, so a real capitalisation typo remains visible if it is ever
    the only thing that differs and the surrounding evidence points that way — removes the
    noise without hiding anything: no DwC field compared here is meaningfully
    case-sensitive (a place, a name, an enum word means the same thing regardless of case).
    """
    local_row = dwc_export.occurrence_row(session, co)
    diffs: list[str] = []
    for field_name in _DIFF_FIELDS:
        local_val = (local_row.get(field_name) or "").strip()
        tw_val = str(tw_row.get(field_name) or "").strip()
        if local_val.casefold() != tw_val.casefold():
            diffs.append(f"{field_name}: local='{local_val}' vs TW='{tw_val}'")
    return tuple(diffs)


def ineligible_specimens(
    session: Session, *, repository_id: int
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Local-only (no network) — every specimen in `repository_id` that
    `export_decision` withholds, with its reasons. #149 step 1.2 computes this per
    specimen; this is the "which ones, and why" detail behind the Collections report's
    bare not-eligible count (typically a confidential person in `recordedBy`)."""
    cos = (
        session.query(CollectionObject)
        .filter(CollectionObject.repository_id == repository_id)
        .all()
    )
    out: list[tuple[str, tuple[str, ...]]] = []
    for co in cos:
        decision = dwc_export.export_decision(co)
        if not decision.eligible:
            out.append((co.catalog_number, decision.reasons))
    return tuple(out)


def compare_repository(
    session: Session, tw_by_cat: dict[str, list[dict]], *, repository_id: int
) -> CompareResult:
    """Pure computation, no I/O — `tw_by_cat` is the (already fetched)
    `{catalog_number: [row, ...]}` map from `fetch_tw_rows_for_catalog_numbers`,
    `session` supplies the local side. Split out from the fetch so this half is
    trivially testable without a live server.
    """
    cos = (
        session.query(CollectionObject)
        .filter(CollectionObject.repository_id == repository_id)
        .all()
    )

    not_on_tw: list[str] = []
    diverged: list[DivergedRow] = []
    duplicates: list[DuplicateGroup] = []
    leaked: list[LeakedRow] = []
    eligible_n = 0
    ineligible_n = 0
    synced_n = 0

    for co in cos:
        decision = dwc_export.export_decision(co)
        cat = co.catalog_number
        matches = tw_by_cat.get(cat, [])

        if decision.eligible:
            eligible_n += 1
            if not matches:
                not_on_tw.append(cat)
            else:
                diffs = _diff_one(session, co, matches[0])
                if diffs:
                    diverged.append(DivergedRow(
                        catalog_number=cat,
                        tw_object_id=matches[0]["dwc_occurrence_object_id"],
                        field_diffs=diffs,
                    ))
                else:
                    synced_n += 1
        else:
            ineligible_n += 1
            # #149 step 1.4 — confidential locally but TaxonWorks still has it.
            if matches:
                leaked.append(LeakedRow(
                    catalog_number=cat,
                    tw_object_id=matches[0]["dwc_occurrence_object_id"],
                    reasons=decision.reasons,
                ))

        # A duplicate is *every* row TaxonWorks returned for this exact catalogNumber —
        # #149 step 1.3 ("specimen may be transferred from one repository to another"),
        # reported with each row's own institutionCode, never silently picking one.
        if len(matches) > 1:
            duplicates.append(DuplicateGroup(
                catalog_number=cat,
                tw_object_ids=tuple(m["dwc_occurrence_object_id"] for m in matches),
                institution_codes=tuple(m.get("institutionCode") or "" for m in matches),
            ))

    return CompareResult(
        checked_count=len(cos),
        eligible_count=eligible_n,
        ineligible_count=ineligible_n,
        synced_count=synced_n,
        not_on_tw=tuple(not_on_tw),
        diverged=tuple(diverged),
        duplicates=tuple(duplicates),
        leaked=tuple(leaked),
    )
