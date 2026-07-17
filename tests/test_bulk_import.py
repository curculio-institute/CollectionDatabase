"""Staged bulk taxon import (#39), modelled on TaxonWorks' Import Dataset.

Two phases: stage (validate every row, write no taxa) then import (create rows through
the idempotent get_or_create_from_chain seam, which is also the dedup). Resumable via a
cursor; blockers surfaced per distinct reason.
"""
import json

import pytest

from app.models import ImportDataset, ImportDatasetRecord, Taxon
from app.services import bulk_import as bi


_CSV = (
    "scientificName,taxonRank,family,scientificNameAuthorship\n"
    "Otiorhynchus sulcatus,species,Curculionidae,\"(Fabricius, 1775)\"\n"
    "Otiorhynchus,genus,Curculionidae,\n"
    "Curculio nucum,species,Curculionidae,\"Linnaeus, 1758\"\n"
)


def _mk(session, content=_CSV, code="ICZN", name="Beetles"):
    return bi.create_taxon_dataset(
        session, name=name, filename="list.csv", content=content,
        nomenclatural_code=code)


# ── staging writes no taxa, only records ────────────────────────────────────

def test_staging_creates_records_but_no_taxa(session):
    before = session.query(Taxon).count()
    ds = _mk(session)
    assert ds.id and ds.status == "staged"
    assert session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).count() == 3
    assert session.query(Taxon).count() == before          # nothing written yet
    assert bi.progress(session, ds.id)["ready"] == 3


def test_row_resolves_name_and_authorship(session):
    ds = _mk(session)
    rec = (session.query(ImportDatasetRecord)
           .filter_by(import_dataset_id=ds.id, row_index=0).one())
    assert rec.status == "ready"
    assert rec.resolved_name == "Otiorhynchus sulcatus"


def test_inline_authorship_is_split_off_the_name(session):
    # authorship inline in scientificName, no separate column — must not pollute the name
    csv = ("scientificName,taxonRank\n"
           "\"Bembidion minimum (Fabricius, 1792)\",species\n")
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "ready"
    assert rec.resolved_name == "Bembidion minimum"


# ── importing is idempotent (the dedup) and resumable ───────────────────────

def test_import_creates_taxa_and_marks_imported(session):
    ds = _mk(session)
    counts = bi.import_ready(session, ds.id)
    assert counts["imported"] == 3
    assert counts["ready"] == 0
    assert session.query(Taxon).filter_by(scientific_name="Otiorhynchus sulcatus").count() == 1
    # ancestor rows were built too (family + genus)
    assert session.query(Taxon).filter_by(scientific_name="Curculionidae").count() == 1
    assert session.get(ImportDataset, ds.id).status == "completed"


def test_import_links_the_created_taxon_to_its_record(session):
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    rec = (session.query(ImportDatasetRecord)
           .filter_by(import_dataset_id=ds.id, row_index=0).one())
    assert rec.status == "imported"
    assert rec.taxon_id is not None
    assert session.get(Taxon, rec.taxon_id).scientific_name == "Otiorhynchus sulcatus"


def test_reimport_does_not_duplicate(session):
    ds1 = _mk(session, name="First")
    bi.import_ready(session, ds1.id)
    n = session.query(Taxon).count()
    ds2 = _mk(session, name="Second")           # same names again
    bi.import_ready(session, ds2.id)
    assert session.query(Taxon).count() == n    # matched, not duplicated


def test_import_is_resumable_via_cursor(session):
    ds = _mk(session)
    bi.import_ready(session, ds.id, max_records=1)     # first chunk
    assert session.get(ImportDataset, ds.id).import_cursor == 1
    assert bi.progress(session, ds.id)["imported"] == 1
    bi.import_ready(session, ds.id, max_records=100)   # finish
    assert bi.progress(session, ds.id)["imported"] == 3
    assert bi.progress(session, ds.id)["ready"] == 0


# ── blockers are surfaced, not guessed ──────────────────────────────────────

def test_missing_code_blocks_until_dataset_code_is_set(session):
    ds = _mk(session, code=None)                       # no dataset code
    assert bi.progress(session, ds.id)["blocked"] == 3
    assert bi.progress(session, ds.id)["ready"] == 0
    # the resolve-once seam: setting the code flips every row ready
    counts = bi.set_dataset_code(session, ds.id, "ICZN")
    assert counts["ready"] == 3 and counts["blocked"] == 0


def test_single_word_name_without_rank_is_blocked_not_guessed(session):
    csv = "scientificName\nOtiorhynchus\n"            # genus? family? order?
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "blocked"
    assert "taxonRank" in rec.error_message


def test_row_with_no_scientific_name_errors(session):
    csv = "scientificName,taxonRank\n,species\n"
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "errored"


def test_invalid_code_is_blocked_at_staging(session):
    ds = _mk(session, code="ICBN")                     # not a real code
    assert bi.progress(session, ds.id)["blocked"] == 3


def test_blocker_summary_groups_by_reason(session):
    ds = _mk(session, code=None)
    summary = bi.blocker_summary(session, ds.id)
    assert summary and summary[0][1] == 3              # all three share one reason
    assert "nomenclatural code" in summary[0][0]


# ── unknown columns are preserved, nothing dropped ──────────────────────────

def test_unknown_columns_are_kept_in_the_raw_row(session):
    csv = ("scientificName,taxonRank,LEGACY:note\n"
           "Otiorhynchus sulcatus,species,keep me\n")
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert json.loads(rec.data)["LEGACY:note"] == "keep me"


# ── delete keeps imported taxa ──────────────────────────────────────────────

def test_delete_dataset_keeps_imported_taxa(session):
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    n = session.query(Taxon).count()
    bi.delete_dataset(session, ds.id)
    assert session.get(ImportDataset, ds.id) is None
    assert session.query(Taxon).count() == n           # taxa are local rows now
