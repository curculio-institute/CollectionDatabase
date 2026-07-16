"""Staged wholesale import (#39), modelled on TaxonWorks' Import Dataset.

The whole uploaded file becomes a durable `ImportDataset` with one `ImportDatasetRecord`
per source row, so a large import is a two-phase, inspectable, resumable operation rather
than an in-memory parse:

  1. **Stage** (`create_taxon_dataset`, `restage`) — parse and validate every row WITHOUT
     writing any taxon. Each record gets a status: `ready` (importable as-is), `blocked`
     (a reason it cannot yet import — an unresolvable parent, a missing nomenclatural code),
     `errored` (a later import attempt raised), or `imported`. Nothing touches the real
     tables here; the user sees the verdict first.
  2. **Import** (`import_ready`) — create a taxon for each `ready` record through the
     existing idempotent seam `taxa.get_or_create_from_chain`. That seam *is* the
     de-duplication: a name matches its composed `(scientificName, rank)` and is reused,
     never duplicated. The dataset carries a resume cursor so a big import runs in chunks
     and continues after a restart (TW persists `import_start_id` the same way).

Only the **Taxon core** (a name checklist) is implemented — this issue leads with unlinked
taxon names, and importing names sidesteps the invariant that a specimen must carry an
identifier. An Occurrence core can follow as a second kind, exactly as TW splits
checklist.rb from occurrences.rb.

The transform reuses the same name services the row-by-row flow uses
(`split_scientific_name_authorship`, `parse_scientific_name`, `get_or_create_from_chain`),
so there is one tested code path, not a parallel importer.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ImportDataset, ImportDatasetRecord, Taxon
from app.services import taxa as taxa_svc
from app.vocab import NOMENCLATURAL_CODES


# ── header handling ─────────────────────────────────────────────────────────
# A checklist is not a DwC occurrence, so it does not share dwc_import's term set;
# match headers by a normalised key (lowercased, alphanumerics only) against known
# aliases. Unknown columns are kept in `data` untouched, never dropped.

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# rank-column -> the rank it names. Ancestor ranks the file may carry as their own
# columns (a beetle checklist commonly has family + genus columns).
_ANCESTOR_COLUMNS: dict[str, str] = {
    "order": "order", "superfamily": "superfamily", "family": "family",
    "subfamily": "subfamily", "tribe": "tribe", "subtribe": "subtribe",
    "genus": "genus", "subgenus": "subgenus",
}
_SCIENTIFIC_NAME = ("scientificname", "scientific_name", "name", "taxonname")
_AUTHORSHIP = ("scientificnameauthorship", "authorship", "author")
_RANK = ("taxonrank", "rank")
_CODE = ("nomenclaturalcode", "nomencode", "code")


def _row_get(norm_row: dict[str, str], aliases) -> str:
    for a in aliases:
        v = norm_row.get(_norm(a))
        if v and v.strip():
            return v.strip()
    return ""


@dataclass
class StagedRow:
    """The staging verdict for one source row."""
    status: str                         # ready | blocked | errored
    resolved_name: str | None = None
    error_message: str | None = None
    chain: list[dict] = field(default_factory=list)   # root→leaf, for get_or_create_from_chain


def stage_taxon_row(norm_row: dict[str, str], default_code: str | None) -> StagedRow:
    """Validate one row into a StagedRow, building the lineage chain it would import.

    The chain is reconstructed from the file's own ancestor columns (family, genus, …)
    plus the binomial/trinomial parsed out of scientificName — the same shape
    `get_or_create_from_wcvp_data` reconstructs from denormalised columns. Authorship
    lands on the leaf (the named taxon); ancestor rows are created from their name alone.
    """
    raw_name = _row_get(norm_row, _SCIENTIFIC_NAME)
    if not raw_name:
        return StagedRow("errored", error_message="no scientificName in this row")

    # The name may carry its authorship inline; the explicit column wins when present.
    bare_name, inline_author = taxa_svc.split_scientific_name_authorship(raw_name)
    author = _row_get(norm_row, _AUTHORSHIP) or inline_author or None

    code = (_row_get(norm_row, _CODE) or (default_code or "")).strip().upper() or None
    if not code:
        return StagedRow(
            "blocked",
            error_message="no nomenclatural code — set one for the dataset")
    if code not in NOMENCLATURAL_CODES:
        return StagedRow(
            "blocked",
            error_message=f"nomenclatural code {code!r} is not one of "
                          f"{', '.join(NOMENCLATURAL_CODES)}")

    # Leaf rank: an explicit taxonRank wins; otherwise infer from the parsed name.
    genus, subgenus, specific, infra = taxa_svc.parse_scientific_name(bare_name)
    if not genus:
        return StagedRow("errored", error_message=f"cannot parse a name from {raw_name!r}")

    explicit_rank = _row_get(norm_row, _RANK).lower() or None
    if explicit_rank:
        if explicit_rank not in taxa_svc.ranks_for(code):
            return StagedRow(
                "blocked",
                error_message=f"rank {explicit_rank!r} is not valid for {code}")
        leaf_rank = explicit_rank
    else:
        leaf_rank = taxa_svc.rank_from_parse(specific, infra)
        # A lone uninomial with no taxonRank is genuinely ambiguous (genus? family?
        # order?). Guessing it would be the silent wrong value of §2 — surface it.
        if leaf_rank == "genus" and not specific:
            return StagedRow(
                "blocked", resolved_name=bare_name,
                error_message="single-word name needs a taxonRank column "
                              "(is it a genus, family, order?)")

    # Build the chain root→leaf. Ancestor columns first (uninomials), then the parsed
    # genus / subgenus / species / subspecies down to the leaf rank.
    chain: list[dict] = []
    for col, rank in _ANCESTOR_COLUMNS.items():
        if rank in ("genus", "subgenus"):
            continue                                   # taken from the parsed name below
        val = _row_get(norm_row, (col,))
        if val:
            chain.append({"name": val, "rank": rank, "code": code, "authorship": None})

    rank_order = taxa_svc.TAXON_RANKS
    leaf_i = rank_order.index(leaf_rank) if leaf_rank in rank_order else len(rank_order)

    def _below_or_at_leaf(rank: str) -> bool:
        return rank in rank_order and rank_order.index(rank) <= leaf_i

    # genus (always, when the name has one and the leaf is genus-or-below)
    if genus and _below_or_at_leaf("genus"):
        chain.append({"name": genus, "rank": "genus", "code": code, "authorship": None})
    if subgenus and _below_or_at_leaf("subgenus"):
        chain.append({"name": f"{genus} ({subgenus})", "rank": "subgenus",
                      "code": code, "authorship": None})
    if specific and _below_or_at_leaf("species"):
        chain.append({"name": f"{genus} {specific}", "rank": "species",
                      "code": code, "authorship": None})
    if infra and _below_or_at_leaf("subspecies"):
        chain.append({"name": f"{genus} {specific} {infra}", "rank": "subspecies",
                      "code": code, "authorship": None})

    if not chain:
        return StagedRow(
            "blocked", resolved_name=bare_name,
            error_message=f"could not build a lineage for rank {leaf_rank!r}")

    # The authorship belongs to the named taxon = the leaf of the chain.
    chain[-1]["authorship"] = author
    resolved = chain[-1]["name"]
    return StagedRow("ready", resolved_name=resolved, chain=chain)


# ── dataset lifecycle ───────────────────────────────────────────────────────

def _decode(content) -> str:
    if isinstance(content, bytes):
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")
    return content


def _parse_rows(content) -> list[dict[str, str]]:
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or ""): (v or "") for k, v in row.items()} for row in reader]


def create_taxon_dataset(
    session: Session, *, name: str, filename: str | None,
    content, nomenclatural_code: str | None,
) -> ImportDataset:
    """Parse a checklist CSV and stage every row. Nothing is written to the taxon table."""
    rows = _parse_rows(content)
    ds = ImportDataset(
        kind="taxon", name=name, source_filename=filename,
        nomenclatural_code=(nomenclatural_code or None), status="staged",
        import_cursor=0,
    )
    session.add(ds)
    session.flush()

    for i, row in enumerate(rows):
        norm_row = {_norm(k): v for k, v in row.items()}
        staged = stage_taxon_row(norm_row, ds.nomenclatural_code)
        session.add(ImportDatasetRecord(
            import_dataset_id=ds.id, row_index=i, status=staged.status,
            data=json.dumps(row, ensure_ascii=False),
            resolved_name=staged.resolved_name, error_message=staged.error_message,
        ))
    session.flush()
    return ds


def restage(session: Session, dataset_id: int) -> dict[str, int]:
    """Re-run staging for every not-yet-imported record (after the user fixes the
    dataset code, etc.). Imported records are never re-evaluated. Returns the new counts."""
    ds = session.get(ImportDataset, dataset_id)
    if ds is None:
        raise ValueError(f"import dataset {dataset_id} not found")
    recs = (session.query(ImportDatasetRecord)
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status != "imported")
            .all())
    for rec in recs:
        staged = stage_taxon_row(
            {_norm(k): v for k, v in json.loads(rec.data).items()},
            ds.nomenclatural_code)
        rec.status = staged.status
        rec.resolved_name = staged.resolved_name
        rec.error_message = staged.error_message
        rec.updated_at = taxa_svc._utcnow()
    session.flush()
    return progress(session, dataset_id)


def set_dataset_code(session: Session, dataset_id: int, code: str | None) -> dict[str, int]:
    """Change the dataset-level nomenclatural code and re-stage — the resolve-once seam
    (TW's namespace mapping): one setting flips every code-less row ready."""
    ds = session.get(ImportDataset, dataset_id)
    if ds is None:
        raise ValueError(f"import dataset {dataset_id} not found")
    ds.nomenclatural_code = (code or "").strip().upper() or None
    ds.updated_at = taxa_svc._utcnow()
    session.flush()
    return restage(session, dataset_id)


def import_ready(session: Session, dataset_id: int, *, max_records: int = 500) -> dict[str, int]:
    """Import up to `max_records` `ready` records from the resume cursor onward.

    Each row goes through `taxa.get_or_create_from_chain` (idempotent → the dedup). A row
    that raises is marked `errored` with the message and the import continues; the cursor
    still advances past it so a later `retry_errored` re-runs only the failures. Returns
    the progress counts; call again until `remaining` is 0 (the UI loops this)."""
    ds = session.get(ImportDataset, dataset_id)
    if ds is None:
        raise ValueError(f"import dataset {dataset_id} not found")

    ds.status = "importing"
    recs = (session.query(ImportDatasetRecord)
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status == "ready",
                    ImportDatasetRecord.row_index >= ds.import_cursor)
            .order_by(ImportDatasetRecord.row_index)
            .limit(max_records)
            .all())

    for rec in recs:
        staged = stage_taxon_row(
            {_norm(k): v for k, v in json.loads(rec.data).items()},
            ds.nomenclatural_code)
        if staged.status != "ready" or not staged.chain:
            # Drifted since staging (e.g. the dataset code was cleared) — do not import.
            rec.status = staged.status
            rec.error_message = staged.error_message
            rec.updated_at = taxa_svc._utcnow()
            continue
        try:
            leaf = taxa_svc.get_or_create_from_chain(session, staged.chain)
            session.flush()
            rec.taxon_id = leaf.id
            rec.resolved_name = leaf.scientific_name
            rec.status = "imported"
            rec.error_message = None
        except Exception as exc:                      # noqa: BLE001 — reported, not hidden
            session.rollback()
            rec = session.get(ImportDatasetRecord, rec.id)
            rec.status = "errored"
            rec.error_message = str(exc)
        rec.updated_at = taxa_svc._utcnow()
        ds = session.get(ImportDataset, dataset_id)
        ds.import_cursor = rec.row_index + 1
        session.flush()

    ds = session.get(ImportDataset, dataset_id)
    counts = progress(session, dataset_id)
    if counts.get("ready", 0) == 0:
        ds.status = "completed"
    ds.updated_at = taxa_svc._utcnow()
    session.flush()
    return counts


def retry_errored(session: Session, dataset_id: int) -> dict[str, int]:
    """Re-stage errored records back to their staging verdict and rewind the cursor so
    `import_ready` picks up the ones that are ready again."""
    recs = (session.query(ImportDatasetRecord)
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status == "errored")
            .all())
    ds = session.get(ImportDataset, dataset_id)
    min_index = None
    for rec in recs:
        staged = stage_taxon_row(
            {_norm(k): v for k, v in json.loads(rec.data).items()},
            ds.nomenclatural_code)
        rec.status = staged.status
        rec.error_message = staged.error_message
        rec.updated_at = taxa_svc._utcnow()
        if staged.status == "ready":
            min_index = rec.row_index if min_index is None else min(min_index, rec.row_index)
    if min_index is not None:
        ds.import_cursor = min(ds.import_cursor, min_index)
        ds.status = "importing"
    session.flush()
    return progress(session, dataset_id)


# ── reporting ───────────────────────────────────────────────────────────────

def progress(session: Session, dataset_id: int) -> dict[str, int]:
    """Counts per record status + `total` + `remaining` (ready rows still to import)."""
    rows = (session.query(ImportDatasetRecord.status, func.count())
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id)
            .group_by(ImportDatasetRecord.status).all())
    counts = {status: 0 for status in ("ready", "blocked", "imported", "errored")}
    counts.update({status: n for status, n in rows})
    counts["total"] = sum(n for _, n in rows)
    counts["remaining"] = counts["ready"]
    return counts


def blocker_summary(session: Session, dataset_id: int) -> list[tuple[str, int]]:
    """Distinct blocking reasons and how many rows each hits — the resolve-once grid
    (the issue's 'surface the ones that can't be resolved'). Most-common first."""
    rows = (session.query(ImportDatasetRecord.error_message, func.count())
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status.in_(("blocked", "errored")))
            .group_by(ImportDatasetRecord.error_message).all())
    return sorted(((m or "—", n) for m, n in rows), key=lambda t: -t[1])


def list_datasets(session: Session) -> list[ImportDataset]:
    return (session.query(ImportDataset)
            .order_by(ImportDataset.created_at.desc()).all())


def get_dataset(session: Session, dataset_id: int) -> ImportDataset | None:
    return session.get(ImportDataset, dataset_id)


def sample_records(session: Session, dataset_id: int, status: str, limit: int = 20
                   ) -> list[ImportDatasetRecord]:
    return (session.query(ImportDatasetRecord)
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status == status)
            .order_by(ImportDatasetRecord.row_index).limit(limit).all())


def delete_dataset(session: Session, dataset_id: int) -> None:
    """Remove the staged dataset (records cascade). Taxa already imported are local rows
    now and are NOT deleted — same rule as removing a name-source dataset."""
    ds = session.get(ImportDataset, dataset_id)
    if ds is not None:
        session.delete(ds)
        session.flush()
