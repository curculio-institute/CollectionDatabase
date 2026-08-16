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
  The filter is nonetheless **re-verified on every response** (`_verify_filter_applied`)
  instead of trusted once: §5c's "unknown params are silently ignored" hazard applies just
  as much to a param that *stops* applying (a TW upgrade, a rename), and this module's own
  history shows the question is easy to get wrong in either direction.

Reuses `dwc_export.occurrence_row` for the local side of the diff — the identical
projection a real export would write, so "diverged" can never disagree with what the
spreadsheet actually contains. Does not import from `tw_sync.py` or modify either of
those two files; the only surface reused from `taxonworks.py` is its **public** names —
`TaxonWorksUnreachable` (the designated exception contract for "could not reach/
authenticate against TaxonWorks"), `explain_tw_error` (#161 — the single canonical
error-message helper; a local copy had already drifted, missing a 404 branch the
canonical one carries, before the drift was noticed), and `PaginationTracker` (#167 — the
pagination-total bookkeeping shared with `tw_media_compare.py`'s fetch functions, the
same duplication-drift reasoning as `explain_tw_error`) — never that module's
underscore-prefixed helpers, which stay private to it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from collections.abc import Iterable

from app.config import get_config
from app.models import CollectionObject, Repository, Taxon, TaxonDetermination
from app.services import dwc_export, taxa
from app.services.taxonworks import (
    PaginationTracker, TaxonWorksUnreachable, explain_tw_error, web_base,
)

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
# (+ authorship) is the identification. The repository half is not a diff field either:
# collection membership is answered by the namespace on the `/identifiers` index
# (`CollectionMismatch`, `OrphanRow`), which is what TaxonWorks actually files the
# specimen under, rather than by dwc_occurrences' `institutionCode` string.
#
# `stateProvince` is deliberately excluded too (not merely case-folded, dropped outright):
# TaxonWorks re-geocodes the coordinate on its own side and can render the admin name in
# a different language than ours (`stateProvince` is English by policy — CLAUDE.md's
# geography-vocab section) — measured on the sandbox project: "Hesse"/"Hessen",
# "Bavaria"/"Bayern", "Western Greece"/"Dytiki Ellada" for specimens whose coordinate is
# not in dispute at all. Comparing it would flag a difference that carries no action
# (there is nothing to fix — both sides describe the same place), the same reasoning that
# already excludes occurrenceID/institutionCode.
#
# `identificationQualifier` is excluded for a third reason, stronger than either:
# **TaxonWorks never emits it.** Its importer folds the value into the OTU's *name*
# (TW @ 897f385, `dataset_record/darwin_core/occurrence.rb:1573-1576`) and its
# dwc_occurrences projection has no entry for the term at all — no
# `identificationQualifier` in `CollectionObject::DwcExtensions::DWC_OCCURRENCE_MAP`,
# no `dwc_identification_qualifier` in `Shared::Dwc::TaxonDeterminationExtensions`,
# though the column does exist in `db/schema.rb`. So the comparison always reads
# TW=''; it could only ever report a difference, never agreement. (Since 2026-07-26 a
# qualified determination is not exportable at all — `dwc_export._determination_reasons`
# — so this field would now be unreachable as well as wrong.)
_DIFF_FIELDS: tuple[str, ...] = (
    "basisOfRecord", "individualCount", "sex", "preparations", "typeStatus",
    "recordedBy", "eventDate", "verbatimEventDate", "country",
    "verbatimLocality", "scientificName", "scientificNameAuthorship", "taxonRank",
    "identifiedBy",
)


def _base() -> str:
    return get_config().tw_base.rstrip("/")


def _verify_filter_applied(rows: object, catalog_number: str) -> list[dict]:
    """Confirm the `catalogNumber=` filter actually applied; fail loudly if it did not.

    CLAUDE.md §5c: TaxonWorks **silently ignores an unknown query param** and answers
    with the unfiltered table, so a result set is never self-evidently filtered. This is
    checked rather than trusted — and the check must *raise*, not quietly drop the
    foreign rows, because "no rows" is the more dangerous reading of a broken filter:
    the export deliberately excludes what TaxonWorks already holds, so a lookup that
    wrongly finds nothing would re-upload those specimens into a CREATE-ONLY importer
    and mint duplicates that cannot be deleted through the API. Same call
    `tw_sync._exact_candidates` makes on an oversized result set: an unreliable lookup
    is a lookup failure, never a finding.
    """
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise TaxonWorksUnreachable(
            f"the lookup for {catalog_number!r} did not return a list of occurrence "
            f"rows — treating it as a lookup failure rather than as a result."
        )
    foreign = sorted(
        {str(row.get("catalogNumber") or "").strip() for row in rows} - {catalog_number}
    )
    if foreign:
        shown = ", ".join(repr(f) for f in foreign[:3])
        raise TaxonWorksUnreachable(
            f"the lookup for {catalog_number!r} returned {len(rows)} row(s) carrying "
            f"other catalog numbers ({shown}) — the catalogNumber filter did not "
            f"apply. Treating this as a lookup failure: trusting it would either "
            f"invent duplicates or re-upload specimens TaxonWorks already holds."
        )
    return rows


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
            return _verify_filter_applied(r.json(), catalog_number)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504):
                last_exc = exc
            else:
                raise explain_tw_error(exc) from exc
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
    raise explain_tw_error(last_exc) from last_exc


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


# ── The catalog-number identity index (/identifiers) ────────────────────────────
#
# CLAUDE.md §5c: `/identifiers` is the identity source, NOT `dwc_occurrences` (whose
# `catalogNumber` is populated on ~1/500 rows and which is a *generated, cached*
# projection that can lag a fresh import — reading a lagging projection as "not on
# TaxonWorks" would re-upload the specimen into a CREATE-ONLY importer).
#
# **Measured against sandbox.taxonworks.org 2026-07-26** (read-only GETs; the join key
# was NOT what the obvious reading of the route suggests, so this is written down):
# - TaxonWorks **splits** the catalog number across the namespace and the identifier.
#   Our local `JJPC-00001` is stored as `identifier="00001"` under namespace 6057
#   (`short_name="JJPC"`, `delimiter="-"`), and **`cached` = "JJPC-00001"** — the fully
#   rendered form. So `cached` is the join key against `collection_object.catalog_number`;
#   matching on `identifier` scored 0 overlap on all 39 specimens.
#   Consequently `?identifier[]=JJPC-00001` returns **0 rows** while
#   `?identifier[]=00001` returns 1 — the filter works, it just is not on the full form,
#   and there is no `cached` filter. Hence: pull the index and match locally.
# - `type=` and `identifier_object_type=` (scalar **and** `[]` form) both filter
#   correctly. Whole project: 429,830 identifiers, 181 CatalogNumbers, of which 39 sit on
#   CollectionObjects (140 on Containers, 2 on Images) — so the entire index is **one
#   request**, replacing one lookup per local specimen.
# - `extend[]=namespace` **does work on this index route** (embedding id/name/short_name/
#   delimiter), contrary to §5c's general "extend is ignored on index routes" note.
# - Totals come back in the `pagination-total` header.
_CATALOG_NUMBER_TYPE = "Identifier::Local::CatalogNumber"
_INDEX_PER_PAGE = 5000


@dataclass(frozen=True)
class TwCatalogEntry:
    """One TaxonWorks CatalogNumber identifier bound to a CollectionObject."""
    catalog_number: str          # `cached` — the rendered full form, our join key
    identifier: str              # the bare part TW stores under the namespace
    tw_object_id: int            # identifier_object_id == the TW collection_object_id
    namespace_id: int | None
    namespace_short_name: str    # "" when it could not be determined


@dataclass(frozen=True)
class CatalogIndex:
    """Every catalog number TaxonWorks holds on a CollectionObject, by rendered form.

    Existence is answered from here and **never scoped to a namespace**: a specimen
    filed under some other namespace is still on TaxonWorks, and calling it absent would
    re-upload it (identifier uniqueness is per namespace, so TW would accept the
    duplicate without complaint). Namespace scoping belongs only to the *orphan*
    question — see `compare_repository`.
    """
    entries: tuple[TwCatalogEntry, ...]
    by_catalog_number: dict[str, tuple[TwCatalogEntry, ...]]

    def has(self, catalog_number: str) -> bool:
        return catalog_number in self.by_catalog_number

    def get(self, catalog_number: str) -> tuple[TwCatalogEntry, ...]:
        return self.by_catalog_number.get(catalog_number, ())


def _namespace_short_name(row: dict) -> str:
    """The namespace's short name, from `extend[]=namespace` when present.

    Derived from `cached` otherwise rather than trusted: `cached` is short_name +
    delimiter + identifier, so stripping the identifier leaves the prefix. (extend was
    measured to work here, but a silently dropped param must degrade, not lie.)
    """
    ns = row.get("namespace")
    if isinstance(ns, dict):
        short = ns.get("short_name") or ns.get("verbatim_short_name") or ""
        if short:
            return str(short)
    cached = str(row.get("cached") or "")
    ident = str(row.get("identifier") or "")
    if ident and cached.endswith(ident) and len(cached) > len(ident):
        return cached[: len(cached) - len(ident)].strip().strip("-:").strip()
    return ""


def build_catalog_index(rows: Iterable[dict]) -> CatalogIndex:
    """Reduce raw `/identifiers` rows to the index, filtering client-side.

    The type/object-type filters are re-applied here instead of being trusted. That is
    safe *for a whole-table pull* in a way it never is for a per-key existence probe: a
    server filter that silently did not apply only costs bandwidth, and dropping rows
    that genuinely are not CollectionObject catalog numbers cannot invent a wrong answer.
    """
    entries: list[TwCatalogEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("type") != _CATALOG_NUMBER_TYPE:
            continue
        if row.get("identifier_object_type") != "CollectionObject":
            continue
        cached = str(row.get("cached") or "").strip()
        obj_id = row.get("identifier_object_id")
        if not cached or not isinstance(obj_id, int):
            continue
        entries.append(TwCatalogEntry(
            catalog_number=cached,
            identifier=str(row.get("identifier") or "").strip(),
            tw_object_id=obj_id,
            namespace_id=row.get("namespace_id"),
            namespace_short_name=_namespace_short_name(row),
        ))
    by_cat: dict[str, list[TwCatalogEntry]] = {}
    for entry in entries:
        by_cat.setdefault(entry.catalog_number, []).append(entry)
    return CatalogIndex(
        entries=tuple(entries),
        by_catalog_number={k: tuple(v) for k, v in by_cat.items()},
    )


async def fetch_catalog_index(on_progress=None) -> CatalogIndex:
    """Pull every CollectionObject catalog number TaxonWorks holds (all namespaces).

    Paged until as many rows are collected as `pagination-total` promised — a short read
    would understate what TaxonWorks has, and *understating* is the dangerous direction
    (it reads as "not on TaxonWorks" and re-uploads), so it raises instead.
    """
    cfg = get_config()
    if not cfg.tw_base.strip() or not cfg.tw_token.strip():
        raise TaxonWorksUnreachable(
            "TaxonWorks is not configured — Settings → TaxonWorks connection."
        )
    rows: list[dict] = []
    # #167 — shared bookkeeping with tw_media_compare's fetch functions; only the
    # arithmetic moved, GET/retry stays exactly as before (this endpoint's own retry is
    # `_fetch_one_catalog_number`'s job for the field-diff pull, not this identity pull).
    tracker = PaginationTracker()
    page = 1
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            try:
                r = await client.get(
                    f"{_base()}/identifiers",
                    params={
                        "type": _CATALOG_NUMBER_TYPE,
                        "identifier_object_type": "CollectionObject",
                        "extend[]": "namespace",
                        "per": _INDEX_PER_PAGE,
                        "page": page,
                        "project_token": cfg.tw_token,
                    },
                )
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise explain_tw_error(exc) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise explain_tw_error(exc) from exc
            body = r.json()
            if not isinstance(body, list):
                raise TaxonWorksUnreachable(
                    "the identifier index did not return a list of rows — treating it "
                    "as a lookup failure rather than as an empty collection."
                )
            tracker.record_page(r, body)
            rows.extend(body)
            if on_progress is not None:
                on_progress(tracker.received,
                            tracker.total if tracker.total is not None else tracker.received)
            if tracker.is_complete(body, _INDEX_PER_PAGE):
                break
            page += 1
    if tracker.total is not None and tracker.received < tracker.total:
        raise TaxonWorksUnreachable(
            f"the identifier index returned {tracker.received} of {tracker.total} rows "
            f"— an incomplete index would read as 'not on TaxonWorks' and re-upload "
            f"those specimens, so this is treated as a lookup failure."
        )
    return build_catalog_index(rows)


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
    """A specimen this collection would withhold, that TaxonWorks nonetheless has.

    `privacy` separates the two causes, because they are not equally urgent and must not
    be presented as if they were: a confidential specimen / event / collector on a public
    mirror is a privacy breach to correct now, whereas an uncertain determination
    (qualified, or above species) is a curation tidy-up. Both are withheld from every
    future export identically; only the reporting differs.
    """
    catalog_number: str
    tw_object_id: int
    reasons: tuple[str, ...]
    privacy: bool = True
    # Field-level diffs against what TaxonWorks actually holds — the same computation
    # `diverged` runs for eligible specimens, run here too (live review: "JJPC-00010
    # slipped through because the collector's name was added later" — the leak itself
    # says the record needs fixing in TaxonWorks, but not what else in it might already
    # be stale; surfacing both together is what makes the fix a single trip). Empty
    # when TaxonWorks' `dwc_occurrences` projection has no row yet to diff against
    # (the same lag `on_tw_not_compared` already accounts for) — never a claim that
    # nothing differs.
    field_diffs: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrphanRow:
    """A catalog number TaxonWorks holds under OUR namespace that this collection lacks.

    `local_collection` separates the two cases that are indistinguishable from
    TaxonWorks' side: the specimen was **moved** to another local collection (a re-home
    keeps `catalog_number` untouched and only re-points `repository_id`), or it is
    **gone** from the local database entirely. Calling a move a deletion would be the
    silent wrong value of CLAUDE.md §2, so the distinction is drawn from a DB-wide
    catalog-number lookup, not from this collection's rows.
    """
    catalog_number: str
    tw_object_id: int
    namespace_short_name: str
    local_collection: str | None      # None => not in the local database at all


@dataclass(frozen=True)
class CollectionMismatch:
    """Held locally in this collection, but TaxonWorks files it under another namespace.

    Usually the other half of a local re-home: `catalog_number` never changes, so the
    specimen keeps the prefix of the collection it came from while TaxonWorks still has
    it in the old namespace. Reported, never fixed — TW's v1 API has no update or delete
    (CLAUDE.md §5), so this is a manual correction in the TaxonWorks UI.
    """
    catalog_number: str
    tw_object_id: int
    tw_namespace: str
    local_collection: str


@dataclass(frozen=True)
class CompareResult:
    checked_count: int              # local specimens compared (eligible + ineligible)
    eligible_count: int
    ineligible_count: int
    synced: tuple[str, ...]         # eligible, on TW, AND no field differs — catalog
                                     # numbers, so the Explore hand-off (#149 follow-up)
                                     # has something to filter on, not just a count
    not_on_tw: tuple[str, ...]      # eligible locally, catalogNumber not found on TW
    diverged: tuple[DivergedRow, ...]
    duplicates: tuple[DuplicateGroup, ...]
    leaked: tuple[LeakedRow, ...]
    # On TaxonWorks per the identifier index, but its dwc_occurrences projection has not
    # caught up, so the fields could not be diffed. Deliberately NOT folded into
    # `not_on_tw` (that would re-upload it) nor into `synced` (nothing was compared).
    on_tw_not_compared: tuple[str, ...] = ()
    moved: tuple[OrphanRow, ...] = ()
    orphaned: tuple[OrphanRow, ...] = ()
    collection_mismatch: tuple[CollectionMismatch, ...] = ()
    # On TaxonWorks under our namespace, genuinely still held HERE (same repository),
    # but excluded from `cos` — and so from every count above — by `scope_taxon_ids`
    # (design pass, live review, bug fix). Only ever non-empty when a taxon scope is
    # active: without one, every locally-held specimen is already in `cos`, so this
    # case cannot arise. Distinguished from `moved` precisely so a same-collection,
    # scope-excluded specimen is never reported as "held in another local collection"
    # — a real, observed false claim before this field existed.
    excluded_by_scope: tuple[OrphanRow, ...] = ()
    # True when no `CatalogIndex` was supplied, so the "on TaxonWorks but not here"
    # direction was never looked at. Declared for the same reason as
    # `media_not_compared`: a report that silently omits a whole class reads as complete.
    orphans_not_compared: bool = True
    media_not_compared: bool = True

    @property
    def synced_count(self) -> int:
        return len(self.synced)

    @property
    def leaked_privacy(self) -> tuple[LeakedRow, ...]:
        """The confidentiality subset of `leaked` — on TaxonWorks despite being
        withheld here for a PRIVACY reason, as opposed to `leaked_curation` (withheld
        for curatorial reasons: a qualified/below-species determination). A live
        privacy breach, is if anything the more urgent of the two.

        Single source of truth (code review fix, #170 follow-up): `[lk for lk in
        result.leaked if lk.privacy]` used to be re-written at three separate call
        sites in `tw_sync_tab.py` (the flow diagram's Leaked count, its Explore
        click-through, and the page's own issues list) — a future change to what
        counts as a privacy leak needed all three updated by hand, and a missed one
        would desync the diagram's count from the list Explore actually opens."""
        return tuple(lk for lk in self.leaked if lk.privacy)

    @property
    def leaked_curation(self) -> tuple[LeakedRow, ...]:
        """The complement of `leaked_privacy` — see its docstring."""
        return tuple(lk for lk in self.leaked if not lk.privacy)


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

    `scientificName` gets one further, targeted exception: if the specimen is determined
    to a **synonym** locally (CLAUDE.md §2 — a determination may deliberately target a
    synonym, and we freeze the name *as determined*, never silently resolving it), a TW
    value matching the *accepted* name's composed name is also treated as a match, not a
    divergence. Verified by reading TaxonWorks' own source
    (`app/models/concerns/shared/dwc/taxon_determination_extensions.rb`):
    ``target_taxon_name ||= current_valid_taxon_name`` — TaxonWorks' own outward
    `scientificName` is *always* the current valid name, never the as-determined synonym,
    regardless of what was actually typed into the identification. Measured on the sandbox
    project: JJPC-00010 is determined "Entimus formosus" (a synonym; local
    `taxon.accepted_name_usage_id` points at "Entimus sastrei") and TW's own comprehensive
    editor still shows "Entimus formosus" as the identification — but its
    `dwc_occurrences.scientificName` reports "Entimus sastrei". That is TW resolving the
    name for its own API projection, not TW disagreeing with the identification, so it
    must not read as "diverged".
    """
    local_row = dwc_export.occurrence_row(session, co)
    taxon = _current_taxon(co)
    diffs: list[str] = []
    for field_name in _DIFF_FIELDS:
        local_val = (local_row.get(field_name) or "").strip()
        tw_val = str(tw_row.get(field_name) or "").strip()
        if local_val.casefold() == tw_val.casefold():
            continue
        if (
            field_name == "scientificName"
            and taxon is not None
            and taxon.accepted_name_usage_id is not None
        ):
            accepted = session.get(Taxon, taxon.accepted_name_usage_id)
            if accepted is not None:
                accepted_full = taxa.compose_full_name(session, accepted).strip()
                if accepted_full.casefold() == tw_val.casefold():
                    continue
        diffs.append(f"{field_name}: local='{local_val}' vs TW='{tw_val}'")
    return tuple(diffs)


def _current_taxon(co: CollectionObject) -> Taxon | None:
    """The taxon of `co`'s current determination, or None.

    Deliberately duplicates `dwc_export._current_determination`'s `is_current == 1` scan
    and `tw_sync._current_taxon`'s identical duplication of it, rather than importing
    either — both are private to modules this file must not modify (module docstring);
    one dependency-free one-liner, copied three times, is the established convention here
    (see `tw_sync.py`'s own comment making the same call).
    """
    for det in co.determinations:
        if det.is_current == 1:
            return det.taxon
    return None


def _scoped_repo_cos(
    session: Session, *, repository_id: int, scope_taxon_ids: set[int] | None,
):
    """Shared by `ineligible_specimens`/`eligible_specimens` with `compare_repository`'s
    own scoping join (code review fix — these two used to ignore `scope_taxon_ids`
    entirely, so a taxon-restricted Collections report showed a scoped "Not eligible"
    count next to an unscoped detail list/Explore hand-off for the very same number)."""
    q = session.query(CollectionObject).filter(
        CollectionObject.repository_id == repository_id)
    if scope_taxon_ids is not None:
        q = q.join(
            TaxonDetermination,
            (TaxonDetermination.collection_object_id == CollectionObject.id)
            & (TaxonDetermination.is_current == 1),
        ).filter(TaxonDetermination.taxon_id.in_(scope_taxon_ids))
    return q.all()


def ineligible_specimens(
    session: Session, *, repository_id: int, scope_taxon_ids: set[int] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Local-only (no network) — every specimen in `repository_id` that
    `export_decision` withholds, with its reasons. #149 step 1.2 computes this per
    specimen; this is the "which ones, and why" detail behind the Collections report's
    bare not-eligible count (typically a confidential person in `recordedBy`).

    `scope_taxon_ids`, same as `compare_repository`'s: narrows to a taxon + its
    descendants so this detail list agrees with a taxon-restricted report's count."""
    cos = _scoped_repo_cos(
        session, repository_id=repository_id, scope_taxon_ids=scope_taxon_ids)
    out: list[tuple[str, tuple[str, ...]]] = []
    for co in cos:
        decision = dwc_export.export_decision(co)
        if not decision.eligible:
            out.append((co.catalog_number, decision.reasons))
    return tuple(out)


def eligible_specimens(
    session: Session, *, repository_id: int, scope_taxon_ids: set[int] | None = None,
) -> tuple[str, ...]:
    """Local-only (no network) — catalog numbers of every specimen in `repository_id`
    `export_decision` allows to export. The complement of `ineligible_specimens`, kept as
    its own function (rather than deriving it from that one) because the two ask
    different questions — "which, and why not" vs. "which" — and callers of this one
    (the Collections report's Explore hand-off, #149 follow-up) only ever need the plain
    catalog-number list.

    `scope_taxon_ids`: see `ineligible_specimens`."""
    cos = _scoped_repo_cos(
        session, repository_id=repository_id, scope_taxon_ids=scope_taxon_ids)
    return tuple(
        co.catalog_number for co in cos if dwc_export.export_decision(co).eligible
    )


def compare_repository(
    session: Session, tw_by_cat: dict[str, list[dict]], *, repository_id: int,
    index: CatalogIndex | None = None, scope_taxon_ids: set[int] | None = None,
) -> CompareResult:
    """Pure computation, no I/O — `tw_by_cat` is the (already fetched)
    `{catalog_number: [row, ...]}` map from `fetch_tw_rows_for_catalog_numbers` supplying
    the field values, `index` is `fetch_catalog_index`'s identity index, and `session`
    supplies the local side. Split out from the fetches so this half is trivially
    testable without a live server.

    **Existence comes from `index`, the field diff from `tw_by_cat`** — two sources
    because they answer different questions and only one of them is authoritative about
    identity (CLAUDE.md §5c). Without an index the existence half falls back to
    `tw_by_cat` and the orphan direction is not examined at all, which the result
    declares via `orphans_not_compared`.

    **The two scopes are deliberately different.** Existence ignores the namespace: a
    specimen filed under someone else's namespace is still on TaxonWorks, and calling it
    absent would re-upload it (per-namespace identifier uniqueness means TW accepts that
    duplicate silently). The orphan sweep is scoped to *our* namespace: without that,
    every other collection in the project would count as our orphan.

    `scope_taxon_ids` (design pass, live review) is the same optional taxon restriction
    the Export step already applies (its own descendant expansion, `batch_ops.py::
    descendant_taxon_ids`) — narrows which LOCAL specimens are even considered, so
    every count this function reports (`eligible_n`/`not_on_tw`/`synced`/`diverged`/…)
    matches "restricted to this taxon", not the whole collection. That includes
    `duplicates`/`leaked`/`collection_mismatch` — all three are appended inside the
    `for co in cos` loop below, so they inherit the same taxon-scoped `cos`. Only
    `_orphans`' three buckets (`moved`/`excluded_by_scope`/`orphaned`) stay unscoped: an
    orphan has no local record to read a taxon off of, so there is nothing to restrict
    it by.
    """
    cos_q = session.query(CollectionObject).filter(
        CollectionObject.repository_id == repository_id)
    if scope_taxon_ids is not None:
        cos_q = cos_q.join(
            TaxonDetermination,
            (TaxonDetermination.collection_object_id == CollectionObject.id)
            & (TaxonDetermination.is_current == 1),
        ).filter(TaxonDetermination.taxon_id.in_(scope_taxon_ids))
    cos = cos_q.all()
    repo = session.get(Repository, repository_id)
    local_code = str((repo.collection_code if repo is not None else "") or "").strip()

    not_on_tw: list[str] = []
    synced: list[str] = []
    on_tw_not_compared: list[str] = []
    diverged: list[DivergedRow] = []
    duplicates: list[DuplicateGroup] = []
    leaked: list[LeakedRow] = []
    collection_mismatch: list[CollectionMismatch] = []
    eligible_n = 0
    ineligible_n = 0
    local_cats_here = {co.catalog_number for co in cos}

    for co in cos:
        decision = dwc_export.export_decision(co)
        cat = co.catalog_number
        matches = tw_by_cat.get(cat, [])
        entries = index.get(cat) if index is not None else ()
        on_tw = index.has(cat) if index is not None else bool(matches)

        if decision.eligible:
            eligible_n += 1
            if not on_tw:
                not_on_tw.append(cat)
            elif not matches:
                # The identifier index says TaxonWorks has it, but its dwc_occurrences
                # projection has not caught up (CLAUDE.md §5 — generated/cached, may lag
                # a fresh import). Never "not_on_tw": that would re-upload it.
                on_tw_not_compared.append(cat)
            else:
                diffs = _diff_one(session, co, matches[0])
                if diffs:
                    diverged.append(DivergedRow(
                        catalog_number=cat,
                        tw_object_id=matches[0]["dwc_occurrence_object_id"],
                        field_diffs=diffs,
                    ))
                else:
                    synced.append(cat)
        else:
            ineligible_n += 1
            # #149 step 1.4 — confidential locally but TaxonWorks still has it.
            if on_tw:
                tw_object_id = (
                    matches[0]["dwc_occurrence_object_id"] if matches
                    else entries[0].tw_object_id
                )
                # Same diff `_diff_one` runs for an eligible/synced specimen (live
                # review, #170 follow-up) — only possible when the projection has
                # actually caught up (`matches`), same precondition as the eligible
                # branch's own `elif not matches: on_tw_not_compared` a few lines up.
                leaked.append(LeakedRow(
                    catalog_number=cat,
                    tw_object_id=tw_object_id,
                    reasons=decision.reasons,
                    privacy=decision.withheld_for_privacy,
                    field_diffs=_diff_one(session, co, matches[0]) if matches else (),
                ))

        # Filed under a different namespace than this collection — the other half of a
        # local re-home (the catalog number never moves, only `repository_id` does).
        if local_code:
            for entry in entries:
                if entry.namespace_short_name and entry.namespace_short_name != local_code:
                    collection_mismatch.append(CollectionMismatch(
                        catalog_number=cat,
                        tw_object_id=entry.tw_object_id,
                        tw_namespace=entry.namespace_short_name,
                        local_collection=local_code,
                    ))

        # A duplicate is *every* record TaxonWorks holds for this catalog number — #149
        # step 1.3 ("a specimen may be transferred from one repository to another"),
        # reported with each one's own collection, never silently picking one. Taken from
        # the identifier index when available: it is namespace-aware and authoritative,
        # where dwc_occurrences is a projection that may not list the row at all.
        if index is not None:
            if len(entries) > 1:
                duplicates.append(DuplicateGroup(
                    catalog_number=cat,
                    tw_object_ids=tuple(e.tw_object_id for e in entries),
                    institution_codes=tuple(e.namespace_short_name for e in entries),
                ))
        elif len(matches) > 1:
            duplicates.append(DuplicateGroup(
                catalog_number=cat,
                tw_object_ids=tuple(m["dwc_occurrence_object_id"] for m in matches),
                institution_codes=tuple(m.get("institutionCode") or "" for m in matches),
            ))

    moved, excluded_by_scope, orphaned = _orphans(
        session, index, local_code=local_code, local_cats_here=local_cats_here
    )

    return CompareResult(
        checked_count=len(cos),
        eligible_count=eligible_n,
        ineligible_count=ineligible_n,
        synced=tuple(synced),
        not_on_tw=tuple(not_on_tw),
        diverged=tuple(diverged),
        duplicates=tuple(duplicates),
        leaked=tuple(leaked),
        on_tw_not_compared=tuple(on_tw_not_compared),
        moved=moved,
        excluded_by_scope=excluded_by_scope,
        orphaned=orphaned,
        collection_mismatch=tuple(collection_mismatch),
        orphans_not_compared=index is None or not local_code,
    )


def _orphans(
    session: Session, index: CatalogIndex | None, *,
    local_code: str, local_cats_here: set[str],
) -> tuple[tuple[OrphanRow, ...], tuple[OrphanRow, ...], tuple[OrphanRow, ...]]:
    """Split what TaxonWorks holds under our namespace but `local_cats_here` does not
    into (moved, excluded_by_scope, gone) — #149 step 1.4's other direction.

    Needs `local_code` to know which namespace is ours; with none there is nothing to
    scope by and the sweep is skipped rather than guessed (reporting every namespace's
    records as our orphans would be worse than reporting none).

    A DB-wide catalog-number lookup (not scoped to this repository OR to any taxon
    restriction `local_cats_here` may already reflect) resolves each candidate to
    whichever repository actually holds it, if any:
      - a **different** repository code than `local_code` → genuinely **moved** (a
        re-home leaves the catalog number intact and only re-points repository_id);
      - **`local_code` itself** → not moved at all — it is still held HERE, just
        excluded from `local_cats_here` by a taxon scope the caller applied (live
        review, bug fix: reporting this as "moved" was a real false claim — without a
        scope, every locally-held specimen is already in `local_cats_here`, so this
        case cannot arise any other way);
      - not found at all → **gone** from the local database entirely.
    """
    if index is None or not local_code:
        return (), (), ()
    candidates = [
        e for e in index.entries
        if e.namespace_short_name == local_code
        and e.catalog_number not in local_cats_here
    ]
    if not candidates:
        return (), (), ()
    elsewhere: dict[str, str] = {}
    cats = [e.catalog_number for e in candidates]
    for chunk_start in range(0, len(cats), 500):        # keep the IN list well inside
        chunk = cats[chunk_start:chunk_start + 500]     # SQLite's bound-parameter limit
        for cat, code in (
            session.query(CollectionObject.catalog_number, Repository.collection_code)
            .join(Repository, CollectionObject.repository_id == Repository.id)
            .filter(CollectionObject.catalog_number.in_(chunk))
            .all()
        ):
            elsewhere[cat] = code
    moved: list[OrphanRow] = []
    excluded: list[OrphanRow] = []
    gone: list[OrphanRow] = []
    for entry in candidates:
        row = OrphanRow(
            catalog_number=entry.catalog_number,
            tw_object_id=entry.tw_object_id,
            namespace_short_name=entry.namespace_short_name,
            local_collection=elsewhere.get(entry.catalog_number),
        )
        if row.local_collection is None:
            gone.append(row)
        elif row.local_collection == local_code:
            excluded.append(row)
        else:
            moved.append(row)
    return tuple(moved), tuple(excluded), tuple(gone)
