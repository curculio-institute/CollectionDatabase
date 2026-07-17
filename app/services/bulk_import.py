"""Staged wholesale import (#39), modelled on TaxonWorks' Import Dataset.

The whole uploaded file becomes a durable `ImportDataset` with one `ImportDatasetRecord`
per source row, so a large import is a two-phase, inspectable, resumable operation rather
than an in-memory parse:

  1. **Stage** — parse and validate every row WITHOUT writing anything. Each record gets a
     status: `ready` (importable as-is), `blocked` (a reason it cannot yet import — a
     missing identifier's collection isn't set up, a name won't resolve, no nomenclatural
     code), `errored` (a hard failure — no catalogNumber, an unparseable date), or
     `imported`. Nothing touches the real tables here; the user sees the verdict first.
  2. **Import** (`import_ready`) — create the records for each `ready` row, resumable via a
     cursor on the dataset (TW persists `import_start_id` the same way).

Two kinds, exactly as TaxonWorks splits `occurrences.rb` from `checklist.rb`:

  - **occurrence** (the primary path, #39) — specimen records that already carry an
     identifier (`catalogNumber`, required — the "every specimen has an identifier"
     invariant, satisfied from the data) and name a target collection (a column, so one file
     can span collections; blank = the home/default). Dedup is the DB's own
     `UNIQUE(repository_id, catalogNumber)`. Each row reuses the taxon resolver below to
     resolve its `scientificName`, and the same event / determination services the row-by-row
     Import & Assign save uses — one tested path, not a parallel importer.
  - **taxon** — a name checklist; created via `create_taxon_dataset`, and the internal
     name-resolution engine the occurrence import reuses (`_chain_for_name` →
     `taxa.get_or_create_from_chain`, idempotent → the name dedup).
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CollectionObject, ImportDataset, ImportDatasetRecord, Repository, Taxon,
)
from app.services import taxa as taxa_svc
from app.services import dwc_import as dwc_svc
from app.services import persons as persons_svc
from app.services import repositories as repo_svc
from app.services import specimens as sp_svc
from app.services.vocabularies import (
    habitat_vocab, preparation_vocab, sampling_protocol_vocab,
)
from app.vocab import NEW_SPECIMEN_DEFAULTS, NOMENCLATURAL_CODES


# Downloadable template for the occurrence (records) bulk import — the columns the importer
# reads, in DwC casing, with two worked rows. Row 1: the home collection (blank `collection`),
# a clean ISO eventDate. Row 2: another collection (by name), a verbatim-only abbreviated-range
# date (parsed automatically), and an open-nomenclature qualifier. Both rows stage `ready`;
# `tests/test_bulk_import_occurrence.py::test_shipped_template_stages_and_imports` guards it.
OCCURRENCE_TEMPLATE_CSV = (
    "catalogNumber,collection,scientificName,scientificNameAuthorship,taxonRank,family,"
    "eventDate,verbatimEventDate,recordedBy,country,countryCode,stateProvince,county,locality,"
    "decimalLatitude,decimalLongitude,coordinateUncertaintyInMeters,"
    "minimumElevationInMeters,maximumElevationInMeters,habitat,samplingProtocol,"
    "sex,individualCount,preparations,lifeStage,typeStatus,"
    "identifiedBy,dateIdentified,identificationQualifier,materialEntityRemarks\n"
    # Home collection (blank collection → the default), clean ISO date.
    "JJPC-00001,,Otiorhynchus sulcatus,\"(Fabricius, 1775)\",species,Curculionidae,"
    "2024-06-15,,J. Doe,Germany,DE,Bavaria,Berchtesgadener Land,"
    "\"Berchtesgaden, Königssee trail\","
    "47.5976,13.0055,50,620,,broadleaf forest edge,hand collecting,"
    "female,3,pinned,adult,,J. Doe,2024-07-01,,\n"
    # Another collection (by its name), eventDate empty with the label date in
    # verbatimEventDate (an abbreviated range, parsed on import), and a cf. qualifier.
    "NHMW-00042,Naturhistorisches Museum Wien,Curculio nucum,\"Linnaeus, 1758\",species,"
    "Curculionidae,,28.-30.08.2023,A. Mayer,Austria,AT,Styria,,"
    "\"Grazer Bergland, Schöckel\","
    "47.1833,15.4667,100,1250,,Fagus-Quercus forest,beating,"
    ",1,pinned,adult,,A. Mayer,2023-09-10,cf.,reared from hazel nuts\n"
)


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


def _resolve_code(norm_row: dict[str, str], default_code: str | None
                  ) -> tuple[str | None, str | None]:
    """(code, error). The row's own `nomenclaturalCode` wins over the dataset default;
    the code is validated against the closed list, never guessed (CLAUDE.md §2)."""
    code = (_row_get(norm_row, _CODE) or (default_code or "")).strip().upper() or None
    if not code:
        return None, "no nomenclatural code — set one for the dataset"
    if code not in NOMENCLATURAL_CODES:
        return None, (f"nomenclatural code {code!r} is not one of "
                      f"{', '.join(NOMENCLATURAL_CODES)}")
    return code, None


def _chain_for_name(norm_row: dict[str, str], code: str
                    ) -> tuple[list[dict] | None, str | None, str]:
    """Build the root→leaf lineage chain for a row's scientificName. Returns
    ``(chain, error, resolved_name)`` — chain is None with a reason when the name cannot
    be turned into a lineage. Shared by the taxon checklist and the occurrence importers.

    The chain is reconstructed from the file's own ancestor columns (family, genus, …)
    plus the binomial/trinomial parsed out of scientificName — the same shape
    `get_or_create_from_wcvp_data` reconstructs from denormalised columns. Authorship
    lands on the leaf (the named taxon); ancestor rows are created from their name alone.
    """
    raw_name = _row_get(norm_row, _SCIENTIFIC_NAME)
    if not raw_name:
        return None, "no scientificName in this row", ""

    bare_name, inline_author = taxa_svc.split_scientific_name_authorship(raw_name)
    author = _row_get(norm_row, _AUTHORSHIP) or inline_author or None

    genus, subgenus, specific, infra = taxa_svc.parse_scientific_name(bare_name)
    if not genus:
        return None, f"cannot parse a name from {raw_name!r}", bare_name

    explicit_rank = _row_get(norm_row, _RANK).lower() or None
    if explicit_rank:
        if explicit_rank not in taxa_svc.ranks_for(code):
            return None, f"rank {explicit_rank!r} is not valid for {code}", bare_name
        leaf_rank = explicit_rank
    else:
        leaf_rank = taxa_svc.rank_from_parse(specific, infra)
        # A lone uninomial with no taxonRank is genuinely ambiguous (genus? family?
        # order?). Guessing it would be the silent wrong value of §2 — surface it.
        if leaf_rank == "genus" and not specific:
            return (None, "single-word name needs a taxonRank column "
                    "(is it a genus, family, order?)", bare_name)

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
        return None, f"could not build a lineage for rank {leaf_rank!r}", bare_name

    chain[-1]["authorship"] = author            # authorship belongs to the named leaf
    return chain, None, chain[-1]["name"]


def stage_taxon_row(norm_row: dict[str, str], default_code: str | None) -> StagedRow:
    """Validate one checklist row into a StagedRow, building the lineage it would import."""
    code, code_err = _resolve_code(norm_row, default_code)
    if code_err:
        # A missing scientificName is a hard error even before the code; check it first.
        if not _row_get(norm_row, _SCIENTIFIC_NAME):
            return StagedRow("errored", error_message="no scientificName in this row")
        return StagedRow("blocked", error_message=code_err)

    chain, err, resolved = _chain_for_name(norm_row, code)
    if err:
        status = "errored" if "no scientificName" in err or "cannot parse" in err else "blocked"
        return StagedRow(status, resolved_name=resolved or None, error_message=err)
    return StagedRow("ready", resolved_name=resolved, chain=chain)


# ── occurrence (specimen record) staging ────────────────────────────────────
# The primary bulk-import path (#39): specimen records that already carry an identifier
# and name a target collection. The identifier is required (the invariant that every
# specimen has a catalogNumber is satisfied from the data, not auto-assigned), the
# collection is resolved from a column (so one file can span collections), and each row
# reuses the taxon name-resolution above.

_CATALOG = ("catalogNumber", "catalog_number", "catalognr", "cat_no")
_COLLECTION = ("collection", "collectionName", "collectionCode",
               "collectionFullName", "repository")


def _date_overrides(row: dict) -> tuple[dict, str | None]:
    """`dwc_import.normalise_row_dates`, but fall back to `verbatimEventDate` when
    `eventDate` is empty. Reference/label data is routinely verbatim-only (every row of the
    Käfersammlung export), and a bulk import cannot ⚡-parse each row by hand as Import &
    Assign does — so the deterministic parse runs automatically here, while the verbatim
    string is always preserved (so a European DD.MM misread stays auditable, §2/#1). An
    unparseable date still refuses the row loudly rather than storing a raw string in
    `dwc:eventDate`."""
    if not (row.get("eventDate") or "").strip() and (row.get("verbatimEventDate") or "").strip():
        row = {**row, "eventDate": row["verbatimEventDate"]}
    return dwc_svc.normalise_row_dates(row)


def _resolve_repo(session: Session, coll_name: str) -> tuple[int | None, str | None]:
    """(repository_id, error). An empty collection column → the home/default collection.
    A **named** collection must already exist (matched on its full name or its code) — its
    catalog-number format belongs to it, so we never fabricate one from an import (§2)."""
    name = (coll_name or "").strip()
    if not name:
        d = repo_svc.get_default(session)
        if d is None:
            return None, ("no collection given and no default collection is set "
                          "(pick one in Settings)")
        return d.id, None
    r = (session.query(Repository)
         .filter(func.lower(Repository.collection_full_name) == name.lower())
         .first()
         or session.query(Repository)
         .filter(func.lower(Repository.collection_code) == name.lower())
         .first())
    if r is None:
        return None, (f"collection {name!r} is not set up — add it in "
                      "Controlled Vocabularies first")
    return r.id, None


def stage_occurrence_row(session: Session, row: dict[str, str],
                         default_code: str | None) -> StagedRow:
    """Validate one specimen row. `row` is the canonical DwC-keyed row (parse_csv output).

    Order of checks mirrors what makes a row importable: an identifier, a resolvable name,
    an existing target collection, a parseable date. Each failure is surfaced with its
    reason rather than guessed past."""
    norm_row = {_norm(k): v for k, v in row.items()}
    catalog = _row_get(norm_row, _CATALOG)
    if not catalog:
        return StagedRow(
            "errored",
            error_message="no catalogNumber — a bulk-imported specimen must already "
                          "carry its identifier")

    code, code_err = _resolve_code(norm_row, default_code)
    if code_err:
        return StagedRow("blocked", resolved_name=catalog, error_message=code_err)

    chain, err, rname = _chain_for_name(norm_row, code)
    if err:
        status = "errored" if ("cannot parse" in err or "no scientificName" in err) else "blocked"
        return StagedRow(status, resolved_name=f"{catalog} · {rname or '?'}",
                         error_message=err)

    repo_id, repo_err = _resolve_repo(session, _row_get(norm_row, _COLLECTION))
    if repo_err:
        return StagedRow("blocked", resolved_name=f"{catalog} · {rname}",
                         error_message=repo_err)

    # eventDate / dateIdentified must parse — a DD.MM misread must never land verbatim in
    # dwc:eventDate (§2). normalise_row_dates refuses the row loudly.
    _, date_err = _date_overrides(row)
    if date_err:
        return StagedRow("errored", resolved_name=f"{catalog} · {rname}",
                         error_message=date_err)

    return StagedRow("ready", resolved_name=f"{catalog} · {rname}", chain=chain)


def _import_occurrence_record(session: Session, row: dict[str, str],
                              chain: list[dict]) -> tuple[CollectionObject, bool, str]:
    """Create the specimen + event + determination for a ready occurrence row, reusing the
    same services the row-by-row Import & Assign save uses. Returns (collection_object,
    was_already_present, display_name). Dedup is the DB's own UNIQUE(repository_id,
    catalogNumber): an existing pair is returned untouched, never duplicated."""
    norm_row = {_norm(k): v for k, v in row.items()}
    catalog = _row_get(norm_row, _CATALOG)
    repo_id, repo_err = _resolve_repo(session, _row_get(norm_row, _COLLECTION))
    if repo_err:
        raise ValueError(repo_err)

    existing = (session.query(CollectionObject)
                .filter(CollectionObject.repository_id == repo_id,
                        CollectionObject.catalog_number == catalog)
                .first())
    if existing is not None:
        return existing, True, catalog

    taxon = taxa_svc.get_or_create_from_chain(session, chain)
    session.flush()

    date_over, date_err = _date_overrides(row)
    if date_err:
        raise ValueError(date_err)
    di_iso = date_over.pop("date_identified", "")

    event_fields = dwc_svc.row_to_event_fields(row)
    _rec = (event_fields.pop("recorded_by", None) or "").strip()
    event_fields["recorded_by_id"] = (
        persons_svc.get_or_create_person(session, full_name=_rec).id if _rec else None)
    _hab = (event_fields.pop("habitat", None) or "").strip()
    event_fields["habitat_id"] = (
        habitat_vocab.get_or_create(session, _hab).id if _hab else None)
    _samp = (event_fields.pop("sampling_protocol", None) or "").strip()
    event_fields["sampling_protocol_id"] = (
        sampling_protocol_vocab.get_or_create(session, _samp).id if _samp else None)
    event_fields.update(date_over)          # ISO event_date [+ verbatim_event_date]

    det = dwc_svc.row_to_determination_fields(row)
    _idby = (det.get("identified_by") or "").strip()
    idby_id = (persons_svc.get_or_create_person(session, full_name=_idby).id
               if _idby else None)

    sp = dwc_svc.row_to_specimen_prefill(row)
    _prep = (sp.get("preparations") or "").strip()
    prep_id = preparation_vocab.get_or_create(session, _prep).id if _prep else None
    try:
        count = int(sp.get("individual_count") or 1)
    except (TypeError, ValueError):
        count = 1

    co = sp_svc.save_specimen_entry(
        session,
        taxon_id=taxon.id,
        event_id=None,
        event_fields=event_fields,
        specimen_fields={
            "catalog_number":     catalog,
            "repository_id":      repo_id,
            "individual_count":   count,
            "preparation_id":     prep_id,
            "life_stage":         sp.get("life_stage") or NEW_SPECIMEN_DEFAULTS["life_stage"],
            "basis_of_record":    NEW_SPECIMEN_DEFAULTS["basis_of_record"],
            "occurrence_remarks": sp.get("occurrence_remarks") or "",
        },
        determination_fields={
            "sex":                      det.get("sex") or None,
            "type_status":              det.get("type_status") or None,
            "identified_by_id":         idby_id,
            "date_identified":          di_iso or None,
            "identification_qualifier": det.get("identification_qualifier") or None,
            "identification_remarks":   det.get("identification_remarks") or None,
            "verbatim_identification":  det.get("verbatim_identification"),
        },
    )
    # The identifier is foreign / already assigned, so there is no reserved label code to
    # bind — the Visiting-mode policy (code=None): store catalogNumber, queue no labels.
    sp_svc.finalize_specimen(session, collection_object_id=co.id, code=None,
                             queue_labels=False)
    return co, False, f"{catalog} · {taxon.scientific_name}"


# ── dataset lifecycle (kind-generic) ────────────────────────────────────────

def _stage_stored_row(session: Session, ds: ImportDataset, row: dict) -> StagedRow:
    """Stage a stored row according to the dataset's kind."""
    if ds.kind == "occurrence":
        return stage_occurrence_row(session, row, ds.nomenclatural_code)
    return stage_taxon_row({_norm(k): v for k, v in row.items()}, ds.nomenclatural_code)


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


def _create_dataset(
    session: Session, *, kind: str, name: str, filename: str | None,
    rows: list[dict], nomenclatural_code: str | None,
) -> ImportDataset:
    ds = ImportDataset(
        kind=kind, name=name, source_filename=filename,
        nomenclatural_code=(nomenclatural_code or None), status="staged",
        import_cursor=0,
    )
    session.add(ds)
    session.flush()
    for i, row in enumerate(rows):
        staged = _stage_stored_row(session, ds, row)
        session.add(ImportDatasetRecord(
            import_dataset_id=ds.id, row_index=i, status=staged.status,
            data=json.dumps(row, ensure_ascii=False),
            resolved_name=staged.resolved_name, error_message=staged.error_message,
        ))
    session.flush()
    return ds


def create_taxon_dataset(
    session: Session, *, name: str, filename: str | None,
    content, nomenclatural_code: str | None,
) -> ImportDataset:
    """Parse a checklist CSV and stage every row. Nothing is written to the taxon table."""
    return _create_dataset(
        session, kind="taxon", name=name, filename=filename,
        rows=_parse_rows(content), nomenclatural_code=nomenclatural_code)


def create_occurrence_dataset(
    session: Session, *, name: str, filename: str | None,
    content, nomenclatural_code: str | None,
) -> ImportDataset:
    """Parse a specimen-record CSV and stage every row. Nothing is written to the specimen
    tables. Rows are normalised to canonical DwC keys (`dwc_import.parse_csv`) so the same
    row_to_* helpers the row-by-row importer uses apply unchanged."""
    return _create_dataset(
        session, kind="occurrence", name=name, filename=filename,
        rows=dwc_svc.parse_csv(content), nomenclatural_code=nomenclatural_code)


def restage(session: Session, dataset_id: int) -> dict[str, int]:
    """Re-run staging for every not-yet-imported record (after the user fixes the
    dataset code, adds a collection, etc.). Imported records are never re-evaluated."""
    ds = session.get(ImportDataset, dataset_id)
    if ds is None:
        raise ValueError(f"import dataset {dataset_id} not found")
    recs = (session.query(ImportDatasetRecord)
            .filter(ImportDatasetRecord.import_dataset_id == dataset_id,
                    ImportDatasetRecord.status != "imported")
            .all())
    for rec in recs:
        staged = _stage_stored_row(session, ds, json.loads(rec.data))
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
        row = json.loads(rec.data)
        staged = _stage_stored_row(session, ds, row)
        if staged.status != "ready" or not staged.chain:
            # Drifted since staging (e.g. the dataset code was cleared) — do not import.
            rec.status = staged.status
            rec.error_message = staged.error_message
            rec.updated_at = taxa_svc._utcnow()
            continue
        try:
            if ds.kind == "occurrence":
                co, _existed, disp = _import_occurrence_record(session, row, staged.chain)
                session.flush()
                rec.collection_object_id = co.id
                rec.resolved_name = disp
            else:
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
        staged = _stage_stored_row(session, ds, json.loads(rec.data))
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
