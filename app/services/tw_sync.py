"""TaxonWorks name pre-flight + OTU-id reconciliation (#149).

Before a spreadsheet is emitted, every taxon name it would carry is checked against the
*currently configured* TaxonWorks instance, so the user can add a missing name in TW by
hand before the export — creating it after the fact is not possible (the DwC importer is
CREATE-ONLY and does not upsert, and biological associations / synonymy cannot be pushed
at all, see CLAUDE.md §5). The same pass reconciles `taxon.taxonworks_otu_id`, because both
questions reduce to one operation: *which TW entity does this local taxon correspond to on
this instance, by name?*

Two hazards drive the design:

1. **A false "missing" is worse than no check at all.** It sends the user to hand-create a
   name that already exists, producing exactly the homonym duplicates the check exists to
   avoid — so the subgenus fallback (`_classify`) is mandatory, not optional.
2. **A stored OTU id from another instance points at a different animal.** OTU ids are
   per-project integers; the same integer denotes a different entity on another server, so a
   stored id is only trustworthy when it was captured on the instance we are talking to now
   (`otu_instance_state`), and even then it is re-verified in reverse (`stored_otu_verdict`),
   never merely compared.

This module is read-only except for `reconcile_otu_ids`, which is called explicitly and
never as a side effect of `check_names`.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import get_config, save_config
from app.models import CollectionObject, Taxon
from app.services import taxa
from app.services import taxonworks as tw
from app.services.dwc_export import export_decision

# A shared public server — bound concurrency so a check run never fires a burst of
# requests at it (mirrors the Overpass/Photon discipline documented in CLAUDE.md).
_MAX_CONCURRENCY = 4

# TW's `name[]`+`name_exact` filter is documented as exact against `cached`, but the
# "unknown params are silently ignored" hazard (§1) means a filter that failed to apply
# returns the whole table. A single name's exact hit count is realistically 1-3 (homonyms
# are rare); anything past this is treated as "the filter did not apply", never as matches.
_UNFILTERED_DUMP_THRESHOLD = 200

_WHITESPACE_RE = re.compile(r"\s+")


def _collapse_ws(value: str | None) -> str:
    """Collapse internal whitespace and strip — the comparison basis throughout this
    module, since TW's `cached` and our composed name can differ only in spacing."""
    return _WHITESPACE_RE.sub(" ", (value or "").strip())


def _strip_parens(value: str) -> str:
    """Drop one layer of enclosing parentheses, e.g. "(Say, 1830)" -> "Say, 1830".

    TW uses parentheses around an authorship to mark a subsequent combination (the name
    was moved to a different genus since that author described it) — not a different
    authorship. Comparing local vs TW authorship must not flag that as a mismatch.
    """
    if len(value) >= 2 and value.startswith("(") and value.endswith(")"):
        return value[1:-1].strip()
    return value


# ── Data shapes ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TwCandidate:
    taxon_name_id: int
    cached: str                  # TW's composed name, no authorship
    authorship: str | None       # cached_author_year
    rank: str | None
    is_valid: bool
    valid_taxon_name_id: int | None
    nomenclatural_code: str | None


@dataclass(frozen=True)
class NameCheck:
    taxon_id: int
    local_name: str              # compose_scientific_name (bare, what we queried)
    local_authorship: str | None
    local_rank: str | None
    status: str                  # "match" | "missing" | "ambiguous" | "rank_mismatch" | "error"
    matched: TwCandidate | None
    candidates: tuple[TwCandidate, ...]   # all candidates seen (for ambiguous/rank_mismatch)
    notes: tuple[str, ...]       # informational, never blocking
    stored_otu_id: int | None    # taxon.taxonworks_otu_id as it stands now
    otu_id: int | None           # the OTU id resolved on THIS instance (None if unresolved)
    # "absent" | "mismatch" | "confirmed" | "unchecked" (no stored id, or the lookup failed).
    stored_otu_verdict: str = "unchecked"

    @property
    def blocks_export(self) -> bool:
        return self.status in {"missing", "ambiguous", "rank_mismatch", "error"}


def _candidate_from(item: dict) -> TwCandidate:
    return TwCandidate(
        taxon_name_id=item["id"],
        cached=item.get("cached") or "",
        authorship=item.get("cached_author_year"),
        rank=item.get("rank"),
        is_valid=bool(item.get("cached_is_valid", True)),
        valid_taxon_name_id=item.get("cached_valid_taxon_name_id"),
        nomenclatural_code=item.get("nomenclatural_code"),
    )


def _error_check(
    taxon: Taxon,
    *,
    local_name: str,
    local_authorship: str | None,
    local_rank: str | None,
    stored_otu_id: int | None,
    reason: str,
) -> NameCheck:
    """Build the `status="error"` shape used on any HTTP failure — never "missing" for a
    failed lookup (§3.4)."""
    return NameCheck(
        taxon_id=taxon.id,
        local_name=local_name,
        local_authorship=local_authorship,
        local_rank=local_rank,
        status="error",
        matched=None,
        candidates=(),
        notes=(reason,),
        stored_otu_id=stored_otu_id,
        otu_id=None,
        stored_otu_verdict="unchecked",
    )


async def _exact_candidates(query_name: str) -> list[TwCandidate]:
    """Run the exact filter and reduce to genuine `cached` equality.

    TW's `name_exact` also matches `cached_original_combination` (per the documented
    filter), so a result set is filtered down to real `cached` equality here — anything
    else is a different name that merely shares an original combination.

    Raises `TaxonWorksUnreachable` both on a real HTTP failure and when the result count
    exceeds `_UNFILTERED_DUMP_THRESHOLD`, treating an apparently-unfiltered dump as a
    lookup failure rather than as candidates (§1).
    """
    raw = await tw.fetch_taxon_names_exact(query_name)
    if len(raw) > _UNFILTERED_DUMP_THRESHOLD:
        raise tw.TaxonWorksUnreachable(
            f"query for {query_name!r} returned {len(raw)} rows — the name[] filter "
            f"did not apply (an applied exact filter never returns this many); treating "
            f"as a lookup failure rather than trusting an unfiltered dump."
        )
    target = _collapse_ws(query_name)
    return [c for c in (_candidate_from(item) for item in raw) if _collapse_ws(c.cached) == target]


# ── Classification (§3) ─────────────────────────────────────────────────────────

async def _check_one(session: Session, sem: asyncio.Semaphore, taxon: Taxon) -> NameCheck:
    local_name = taxa.compose_scientific_name(session, taxon)
    local_authorship = taxon.scientific_name_authorship
    local_rank = taxon.taxon_rank
    stored_otu_id = taxon.taxonworks_otu_id

    async with sem:
        try:
            hits = await _exact_candidates(local_name)
        except tw.TaxonWorksUnreachable as exc:
            return _error_check(
                taxon, local_name=local_name, local_authorship=local_authorship,
                local_rank=local_rank, stored_otu_id=stored_otu_id, reason=str(exc),
            )

        notes: list[str] = []
        matched: TwCandidate | None = None
        candidates: tuple[TwCandidate, ...] = ()
        status: str

        if not hits:
            # Subgenus fallback (mandatory per §3.1): subgenus placement is unstable
            # across catalogues and often simply omitted, so a subgenus-only difference
            # must not be reported as "missing" — that would send the user to create a
            # duplicate that already exists under a different subgenus bracket.
            key = taxa.binomial_key(local_name)
            if key != local_name:
                try:
                    fallback_hits = await _exact_candidates(key)
                except tw.TaxonWorksUnreachable as exc:
                    return _error_check(
                        taxon, local_name=local_name, local_authorship=local_authorship,
                        local_rank=local_rank, stored_otu_id=stored_otu_id, reason=str(exc),
                    )
                if (
                    len(fallback_hits) == 1
                    and local_rank
                    and (fallback_hits[0].rank or "").lower() == local_rank.lower()
                ):
                    matched = fallback_hits[0]
                    status = "match"
                    notes.append(
                        f"TW places this name without the subgenus: '{matched.cached}' "
                        f"— subgenus placement differs"
                    )
                else:
                    status = "missing"
            else:
                status = "missing"
        elif len(hits) == 1:
            matched = hits[0]
            status = "match"
        else:
            survivors = [
                c for c in hits
                if local_rank and (c.rank or "").lower() == local_rank.lower()
            ]
            if len(survivors) == 1:
                matched = survivors[0]
                status = "match"
                notes.append(
                    f"TW has {len(hits)} names with this spelling; matched on rank "
                    f"{matched.rank}"
                )
            elif not survivors:
                status = "rank_mismatch"
                candidates = tuple(hits)
                ranks = ", ".join(sorted({c.rank or "(no rank)" for c in hits}))
                notes.append(f"TW has this spelling only at rank(s): {ranks}")
            else:
                status = "ambiguous"
                candidates = tuple(survivors)

        otu_id: int | None = None
        if status == "match" and matched is not None:
            # Informational-only comparisons (§3, never set blocks_export).
            local_auth_c = _strip_parens(_collapse_ws(local_authorship))
            tw_auth_c = _strip_parens(_collapse_ws(matched.authorship))
            if local_auth_c != tw_auth_c and (local_auth_c or tw_auth_c):
                # WHY this matters: TW disambiguates homonyms on authorship, including
                # parentheses and year (occurrence.rb:126-145) — a drifted authorship is
                # worth surfacing even though it never blocks the export.
                notes.append(
                    f"authorship differs — local '{local_authorship or ''}' vs "
                    f"TW '{matched.authorship or ''}'"
                )
            if not matched.is_valid:
                notes.append(
                    f"TW considers this name invalid (a synonym); its valid name id is "
                    f"{matched.valid_taxon_name_id}"
                )
                # Compare with our own view — informational only: a determination may
                # deliberately target a synonym (CLAUDE.md §2), and taxonomicStatus
                # cannot be pushed to TW at all, so neither side is corrected here.
                if taxon.accepted_name_usage_id is not None:
                    notes.append("TW considers this a synonym; locally it is also a synonym")
                else:
                    notes.append("TW considers this a synonym; locally it is accepted")

            try:
                otu_id = await tw.fetch_otu_id_for_taxon_name(matched.taxon_name_id)
            except tw.TaxonWorksUnreachable as exc:
                notes.append(f"could not resolve an OTU id on this instance: {exc}")

        # Reverse OTU verification (§4) — one extra request, only when there is a stored
        # id to check, under the same semaphore slot (we are still inside `async with sem`).
        stored_otu_verdict = "unchecked"
        if stored_otu_id is not None:
            try:
                reverse_hits = await tw.fetch_taxon_names_by_otu(stored_otu_id)
            except tw.TaxonWorksUnreachable as exc:
                notes.append(f"could not verify stored OTU id {stored_otu_id}: {exc}")
            else:
                if not reverse_hits:
                    stored_otu_verdict = "absent"
                else:
                    reverse_names = {_collapse_ws(h.get("cached")) for h in reverse_hits}
                    if _collapse_ws(local_name) in reverse_names:
                        stored_otu_verdict = "confirmed"
                    else:
                        stored_otu_verdict = "mismatch"
                        other = next(iter(reverse_names), "?")
                        notes.append(
                            f"stored OTU id {stored_otu_id} denotes '{other}' on this "
                            f"instance, not '{local_name}' — do not trust it silently"
                        )

    return NameCheck(
        taxon_id=taxon.id,
        local_name=local_name,
        local_authorship=local_authorship,
        local_rank=local_rank,
        status=status,
        matched=matched,
        candidates=candidates,
        notes=tuple(notes),
        stored_otu_id=stored_otu_id,
        otu_id=otu_id,
        stored_otu_verdict=stored_otu_verdict,
    )


# ── OTU reconciliation (§4) ─────────────────────────────────────────────────────

def otu_instance_state() -> tuple[str, str, bool]:
    """(current_host, recorded_host, trusted) — trusted is False when the recorded
    provenance is empty or does not match the currently configured instance."""
    cfg = get_config()
    current_host = urlsplit(cfg.tw_base).netloc
    recorded_host = cfg.tw_otu_instance
    trusted = bool(recorded_host) and recorded_host == current_host
    return current_host, recorded_host, trusted


def reconcile_otu_ids(session: Session, checks: Iterable[NameCheck]) -> int:
    """Write the resolved OTU id for every `match` back onto `taxon.taxonworks_otu_id`,
    then record this instance as the id's provenance. Returns the number of rows changed.

    Read-only `check_names` never calls this — reconciliation is an explicit, separate
    action. Runs in the caller's transaction: no commit here, the caller owns the session.
    """
    changed = 0
    for check in checks:
        if check.status != "match" or check.otu_id is None:
            continue
        taxon = session.get(Taxon, check.taxon_id)
        if taxon is None:
            continue
        if taxon.taxonworks_otu_id != check.otu_id:
            taxon.taxonworks_otu_id = check.otu_id
            changed += 1

    cfg = get_config()
    cfg.tw_otu_instance = urlsplit(cfg.tw_base).netloc
    save_config(cfg)
    return changed


# ── Public entry points (§6) ─────────────────────────────────────────────────────

def _current_taxon(co: CollectionObject) -> Taxon | None:
    """The taxon of `co`'s current determination, or None.

    Deliberately duplicates dwc_export._current_determination's `is_current == 1` scan
    rather than importing it: that helper is private to dwc_export, which this module
    must not modify (see module docstring / spec).
    """
    for det in co.determinations:
        if det.is_current == 1:
            return det.taxon
    return None


def taxa_to_export(session: Session, cos: Iterable[CollectionObject]) -> list[Taxon]:
    """The distinct taxa of the current determinations of specimens `export_decision`
    says are eligible — exactly the names the spreadsheet would carry, nothing more.
    A withheld specimen's name is never checked; a specimen with no current
    determination contributes nothing. Ordered by composed name for a stable report.
    """
    seen: dict[int, Taxon] = {}
    for co in cos:
        if not export_decision(co).eligible:
            continue
        tx = _current_taxon(co)
        if tx is not None:
            seen.setdefault(tx.id, tx)
    return sorted(seen.values(), key=lambda t: taxa.compose_scientific_name(session, t))


async def check_names(session: Session, taxa_list: Sequence[Taxon]) -> list[NameCheck]:
    """Check every taxon in `taxa_list` against the currently configured TaxonWorks
    instance, with bounded concurrency (`_MAX_CONCURRENCY`) — this is a shared public
    server, not fired at in a burst. Read-only: never writes `taxon.taxonworks_otu_id`
    (see `reconcile_otu_ids`).
    """
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    return await asyncio.gather(*(_check_one(session, sem, t) for t in taxa_list))
