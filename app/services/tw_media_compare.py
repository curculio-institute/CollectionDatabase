"""Media comparison between local media_attachment rows and TaxonWorks Depictions
(#149 step 1.6) — read-only + diagnostic, never a push.

**Why diagnostic-only, like biological associations (CLAUDE.md §5):** media is not part
of the DwC occurrence core, so TaxonWorks' DwC-A importer has no path for it at all — and
there is no public API endpoint to *create* an Image either (verified against the
project's local OpenAPI reference, `docs/openapi/image.yaml` / `depiction.yaml`: every
route is a GET). An upload can only happen through TaxonWorks' own web UI. So this module
can only ever report what is missing; it names the gap, links to the right TaxonWorks
page (`tw_compare.edit_url`), and stages the exact files for drag-and-drop so the manual
step is fast — it never attempts to push anything itself.

**The identity bridge — verified live, not assumed (2026-07-26, against
sandbox.taxonworks.org):** TaxonWorks' `/images` endpoint reports
``image_file_fingerprint``. Its own OpenAPI reference claims this is a SHA256; that is
wrong — downloading an existing Image's ``original`` bytes and hashing them locally
produced an exact match to a 32-hex-character value, i.e. **MD5**. Trust the
measurement over the doc, per this project's established practice for TaxonWorks facts
(CLAUDE.md §5c). Content-addressed, so it is unaffected by TaxonWorks renaming the file
on ingest — renaming was the wrong worry; re-encoding would have been the real one, and
the match confirms the ``original`` style is untouched (only the derived thumb/medium
styles are ever reprocessed, standard Paperclip behaviour). Our own store hashes SHA-256
(the de-dup key, ``media.sha256``) — a different algorithm, so MD5 is computed
separately and **cached** on ``media.md5_fingerprint`` (migration 0070) rather than
recomputed on every compare (the user's choice: cache, not compute-on-demand).

**Query params, verified one at a time (2026-07-26) — do not trust the OpenAPI doc's
param names either without checking:**
- ``/depictions?depiction_object_type=CollectionObject&depiction_object_id[]=<ids>`` —
  both filters confirmed real (not silently ignored): scoping to ``CollectionObject``
  alone dropped this project's 6 depictions to 0 (none are attached to a
  CollectionObject yet), and ``depiction_object_id[]`` combined with the type filter
  correctly narrowed to exactly the matching row.
- ``/images?image_id[]=<ids>`` — the correct param, confirmed by a before/after total
  count. ``id[]``, which reads as the obvious guess, is **silently ignored** — measured:
  ``id[]=4332`` returned the same total (9) as no filter at all, i.e. the unfiltered
  table, exactly the CLAUDE.md §5c hazard ("unknown params are silently ignored") this
  project's own convention warns to re-check rather than assume.

Both endpoints accept a batch of ids in one request (verified: two ids in
``image_id[]`` returned exactly those two, not the unfiltered table), so a whole
collection's media compare costs a small, bounded number of requests — the same shape
as the ``/identifiers`` catalog index, not one request per specimen.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import CollectionObject
from app.services import media as media_svc
from app.services.taxonworks import TaxonWorksUnreachable, explain_tw_error
from app.services.tw_compare import CatalogIndex, edit_url

_TIMEOUT = httpx.Timeout(25.0)
_PER_PAGE = 500


def _base() -> str:
    return get_config().tw_base.rstrip("/")


# ── TaxonWorks fetch (I/O) ──────────────────────────────────────────────────────

def _read_total(r: httpx.Response, total: int | None) -> int | None:
    """Read `pagination-total`/`x-total` once (the first page's value is authoritative;
    later pages are not re-parsed) — shared by both paginated fetches below."""
    if total is not None:
        return total
    raw_total = r.headers.get("pagination-total") or r.headers.get("x-total")
    return int(raw_total) if raw_total and raw_total.isdigit() else None


async def fetch_depictions_by_object(collection_object_ids: list[int]) -> dict[int, list[int]]:
    """TW collection_object_id -> [image_id, ...], one bulk paginated pull scoped to
    `depiction_object_type=CollectionObject` + our ids. Empty input never touches the
    network — an empty `[]` id filter is not "everything", it's nothing to ask about."""
    if not collection_object_ids:
        return {}
    cfg = get_config()
    out: dict[int, list[int]] = {}
    total: int | None = None
    received = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        page = 1
        while True:
            try:
                r = await client.get(
                    f"{_base()}/depictions",
                    params={
                        "depiction_object_type": "CollectionObject",
                        "depiction_object_id[]": collection_object_ids,
                        "per": _PER_PAGE,
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
                    "the depictions endpoint did not return a list of rows — treating it "
                    "as a lookup failure rather than as 'no media'."
                )
            total = _read_total(r, total)
            received += len(body)
            for row in body:
                co_id = row.get("depiction_object_id")
                image_id = row.get("image_id")
                if isinstance(co_id, int) and isinstance(image_id, int):
                    out.setdefault(co_id, []).append(image_id)
            if not body or (total is not None and received >= total):
                break
            if len(body) < _PER_PAGE:
                break
            page += 1
    # #160 — a short final page is not proof of "last page": mirrors
    # `tw_compare.fetch_catalog_index`'s pagination-total cross-check, since an
    # incomplete read here understates what TaxonWorks has and reads as "not on
    # TaxonWorks yet", the same dangerous direction that check exists to catch.
    if total is not None and received < total:
        raise TaxonWorksUnreachable(
            f"the depictions endpoint returned {received} of {total} rows — an "
            f"incomplete read would understate this collection's TaxonWorks media, so "
            f"this is treated as a lookup failure rather than as 'no media'."
        )
    return out


async def fetch_image_fingerprints(image_ids: list[int]) -> dict[int, str]:
    """TW image id -> MD5 fingerprint (`image_file_fingerprint` — see module docstring
    for why this is MD5, not the SHA256 the OpenAPI reference claims)."""
    if not image_ids:
        return {}
    cfg = get_config()
    out: dict[int, str] = {}
    total: int | None = None
    received = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        page = 1
        while True:
            try:
                r = await client.get(
                    f"{_base()}/images",
                    params={
                        "image_id[]": image_ids,
                        "per": _PER_PAGE,
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
                    "the images endpoint did not return a list of rows — treating it "
                    "as a lookup failure rather than as 'no fingerprint'."
                )
            total = _read_total(r, total)
            received += len(body)
            for row in body:
                iid = row.get("id")
                fp = row.get("image_file_fingerprint")
                if isinstance(iid, int) and fp:
                    out[iid] = str(fp)
            if not body or (total is not None and received >= total):
                break
            if len(body) < _PER_PAGE:
                break
            page += 1
    if total is not None and received < total:
        raise TaxonWorksUnreachable(
            f"the images endpoint returned {received} of {total} rows — an incomplete "
            f"read would understate which local files are already on TaxonWorks, so "
            f"this is treated as a lookup failure rather than as 'no fingerprint'."
        )
    return out


# ── pure computation ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LocalOnlyMedia:
    media_id: int
    original_filename: str | None
    category: str
    relative_path: str    # on-disk path under media_dir() — for stage_for_upload only


@dataclass(frozen=True)
class SpecimenMediaGap:
    """A specimen known to be on TaxonWorks that has local media TaxonWorks doesn't."""
    catalog_number: str
    collection_object_id: int
    tw_object_id: int
    edit_url: str
    local_only: tuple[LocalOnlyMedia, ...]


@dataclass(frozen=True)
class MediaCompareResult:
    checked_count: int              # specimens on TW examined (whether or not they have local media)
    with_local_media_count: int     # of those, how many have any local media at all
    matched_count: int               # local media files whose fingerprint IS on TW under that specimen
    gaps: tuple[SpecimenMediaGap, ...]   # specimens with >=1 local file missing from TW
    tw_only_count: int                # TW depictions with no matching local fingerprint — informational
                                       # only: nothing to act on locally, reported so the class is
                                       # never silently omitted (CLAUDE.md §2)
    ambiguous_catalog_numbers: tuple[str, ...] = ()
        # #157 — catalog numbers the identifier index reports under MORE than one
        # TaxonWorks record (a cross-namespace duplicate, `tw_compare.DuplicateGroup`'s
        # own condition). Which one is "this" specimen's Depictions cannot be guessed —
        # picking the wrong one would attach a false gap/match to an unrelated
        # specimen's photograph — so these are excluded from the compare and reported
        # here instead, never silently resolved to the first entry.


def specimens_on_tw(
    session: Session, *, repository_id: int, index: CatalogIndex,
) -> tuple[list[tuple[str, int, int]], tuple[str, ...]]:
    """(catalog_number, collection_object_id, tw_object_id) for every LOCAL specimen in
    `repository_id` that the identifier index confirms TaxonWorks holds — independent of
    current export eligibility. A specimen uploaded before the certainty rule (CLAUDE.md
    §5c) existed can still carry TaxonWorks media worth knowing about, so this is not
    gated by `dwc_export.export_decision` the way the occurrence compare is.

    Returns `(specimens, ambiguous_catalog_numbers)` — a catalog number the index reports
    under more than one TaxonWorks record (#157) is excluded from `specimens` rather than
    guessed via `entries[0]`, and listed separately instead."""
    cos = (
        session.query(CollectionObject)
        .filter(CollectionObject.repository_id == repository_id)
        .all()
    )
    out: list[tuple[str, int, int]] = []
    ambiguous: list[str] = []
    for co in cos:
        entries = index.get(co.catalog_number)
        if not entries:
            continue
        if len(entries) > 1:
            ambiguous.append(co.catalog_number)
            continue
        out.append((co.catalog_number, co.id, entries[0].tw_object_id))
    return out, tuple(ambiguous)


def compare_media(
    session: Session,
    specimens: list[tuple[str, int, int]],
    depictions_by_object: dict[int, list[int]],
    fingerprints_by_image: dict[int, str],
    *,
    ambiguous_catalog_numbers: tuple[str, ...] = (),
) -> MediaCompareResult:
    """Pure computation, no I/O — mirrors `tw_compare.compare_repository`'s split so this
    half is trivially testable without a live server."""
    gaps: list[SpecimenMediaGap] = []
    matched = 0
    with_local = 0
    tw_only = 0
    for catalog_number, co_id, tw_object_id in specimens:
        # TaxonWorks Depictions/Images can only ever be images (#156) — a Sound/Document/
        # Sequence/Video/Other attachment can never match a TW image fingerprint, so
        # comparing it would permanently report a "gap" TaxonWorks has no way to close.
        attachments = [
            att for att in media_svc.list_attachments(
                session, target_kind="collection_object", target_id=co_id
            )
            if att.media.category == "Image"
        ]
        tw_fingerprints = {
            fingerprints_by_image[iid]
            for iid in depictions_by_object.get(tw_object_id, ())
            if iid in fingerprints_by_image
        }
        if not attachments:
            # #158 — count distinct resolved fingerprints, matching the branch below
            # (`tw_fingerprints - local_fingerprints_seen`), not raw TW image ids: two
            # Depictions sharing one fingerprint (e.g. a re-uploaded duplicate) must not
            # count differently depending on whether the specimen happens to have any
            # local media at all.
            tw_only += len(tw_fingerprints)
            continue
        with_local += 1
        local_fingerprints_seen: set[str] = set()
        missing: list[LocalOnlyMedia] = []
        for att in attachments:
            md5 = media_svc.ensure_md5(session, att.media)
            if md5 and md5 in tw_fingerprints:
                matched += 1
                local_fingerprints_seen.add(md5)
            else:
                missing.append(LocalOnlyMedia(
                    media_id=att.media.id,
                    original_filename=att.media.original_filename,
                    category=att.media.category,
                    relative_path=att.media.relative_path,
                ))
        tw_only += len(tw_fingerprints - local_fingerprints_seen)
        if missing:
            gaps.append(SpecimenMediaGap(
                catalog_number=catalog_number,
                collection_object_id=co_id,
                tw_object_id=tw_object_id,
                edit_url=edit_url(tw_object_id),
                local_only=tuple(missing),
            ))
    return MediaCompareResult(
        checked_count=len(specimens),
        with_local_media_count=with_local,
        matched_count=matched,
        gaps=tuple(gaps),
        tw_only_count=tw_only,
        ambiguous_catalog_numbers=ambiguous_catalog_numbers,
    )


async def run_media_compare(
    session: Session, *, repository_id: int, index: CatalogIndex,
) -> MediaCompareResult:
    """I/O + computation together — the entrypoint the tab calls."""
    specimens, ambiguous = specimens_on_tw(
        session, repository_id=repository_id, index=index)
    tw_ids = [tw_object_id for _cat, _co_id, tw_object_id in specimens]
    depictions_by_object = await fetch_depictions_by_object(tw_ids)
    image_ids = sorted({iid for ids in depictions_by_object.values() for iid in ids})
    fingerprints_by_image = await fetch_image_fingerprints(image_ids)
    # `compare_media` calls `media_svc.ensure_md5`, which reads whole files off disk and
    # hashes them for every legacy row — real blocking I/O, not a quick DB query. Off the
    # event loop (#155) so a large backfill can't freeze the UI for every connected client;
    # safe because nothing else touches `session` concurrently while this awaits.
    return await asyncio.to_thread(
        compare_media, session, specimens, depictions_by_object, fingerprints_by_image,
        ambiguous_catalog_numbers=ambiguous)


# ── staging for manual upload (drag-and-drop assist) ────────────────────────────────

_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9._ -]')


def _safe_filename(name: str | None, *, fallback: str) -> str:
    if not name or not name.strip():
        return fallback
    cleaned = _UNSAFE_FILENAME.sub("_", Path(name).name).strip(". ")
    return cleaned or fallback


def stage_for_upload(gap: SpecimenMediaGap) -> tuple[Path, tuple[LocalOnlyMedia, ...]]:
    """Copy `gap`'s local-only files into a fresh temp folder, named for drag-and-drop —
    never the canonical content-addressed store itself (that store's filenames are
    content hashes, not names a person would want to see in an upload dialog), and never
    a move: the canonical copy is untouched. Caller opens the returned folder.

    Returns `(staging_dir, skipped)` — `skipped` names any file whose on-disk bytes were
    missing (#159: moved/deleted/corrupted outside the app). That is a real on-disk
    integrity problem, so the caller must report it rather than showing an unconditional
    "files copied" success message — CLAUDE.md's "never skip silently"."""
    safe_cat = _safe_filename(gap.catalog_number, fallback="specimen") \
        .replace(" ", "_")
    staging = Path(tempfile.mkdtemp(prefix=f"tw_upload_{safe_cat}_"))
    seen_names: set[str] = set()
    skipped: list[LocalOnlyMedia] = []
    for item in gap.local_only:
        src = media_svc.abs_path(item.relative_path)
        ext = src.suffix
        name = _safe_filename(item.original_filename, fallback=f"media_{item.media_id}{ext}")
        base, suffix = Path(name).stem, Path(name).suffix
        candidate = name
        n = 1
        while candidate in seen_names:
            n += 1
            candidate = f"{base}_{n}{suffix}"
        seen_names.add(candidate)
        if src.is_file():
            shutil.copy2(src, staging / candidate)
        else:
            skipped.append(item)
    return staging, tuple(skipped)


def open_folder(path: Path) -> None:
    """Best-effort, cross-platform 'open in file manager' — same per-OS branch + never-
    raises shape as `notify.notify`. The folder from `stage_for_upload` contains ONLY the
    files to upload, so opening it (not selecting-within-a-larger-folder) is enough."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            import os
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass   # best-effort; the UI also shows the path as text so the user can navigate manually
