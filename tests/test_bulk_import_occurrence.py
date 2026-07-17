"""Staged bulk import of specimen records (#39, Occurrence core).

Records that already carry an identifier (catalogNumber) and name a target collection.
The identifier is required (the invariant, satisfied from the data), the collection is
resolved from a column (one file can span collections), and each row reuses the taxon
name-resolution engine. Dedup is the DB's UNIQUE(repository_id, catalogNumber).
"""
import json

from app.models import CollectionObject, ImportDataset, ImportDatasetRecord
from app.services import bulk_import as bi
from app.services import repositories as repo_svc


def _repo(session, code, name, default=False):
    r = repo_svc.create_repository(session, collection_code=code, collection_full_name=name)
    session.flush()
    if default:
        repo_svc.set_default(session, r.id)
    return r


_OCC_CSV = (
    "catalogNumber,scientificName,taxonRank,family,eventDate,country,locality,recordedBy,collection\n"
    "JJPC-00001,Otiorhynchus sulcatus,species,Curculionidae,2024-06-15,Germany,Königssee,J. Doe,\n"
    "JJPC-00002,Curculio nucum,species,Curculionidae,28.-30.08.2023,Austria,Schöckel,J. Doe,\n"
)


def _mk(session, content=_OCC_CSV, code="ICZN", name="Records"):
    return bi.create_occurrence_dataset(
        session, name=name, filename="occ.csv", content=content,
        nomenclatural_code=code)


# ── staging writes no specimens ─────────────────────────────────────────────

def test_staging_creates_records_but_no_specimens(session):
    _repo(session, "JJPC", "Home", default=True)
    before = session.query(CollectionObject).count()
    ds = _mk(session)
    assert ds.kind == "occurrence"
    assert bi.progress(session, ds.id)["ready"] == 2
    assert session.query(CollectionObject).count() == before      # nothing written


def test_missing_catalog_number_errors(session):
    _repo(session, "JJPC", "Home", default=True)
    csv = ("catalogNumber,scientificName,taxonRank,eventDate,country\n"
           ",Otiorhynchus sulcatus,species,2024-06-15,Germany\n")
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "errored"
    assert "catalogNumber" in rec.error_message


def test_unparseable_date_errors_not_stored_verbatim(session):
    _repo(session, "JJPC", "Home", default=True)
    csv = ("catalogNumber,scientificName,taxonRank,eventDate,country\n"
           "JJPC-1,Otiorhynchus sulcatus,species,35.13.2020,Germany\n")
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "errored"
    assert "eventDate" in rec.error_message


# ── collection resolution ───────────────────────────────────────────────────

def test_empty_collection_uses_default(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    co = (session.query(CollectionObject)
          .filter_by(catalog_number="JJPC-00001").one())
    assert co.repository.collection_code == "JJPC"


def test_named_collection_targets_that_collection(session):
    home = _repo(session, "JJPC", "Home", default=True)
    other = _repo(session, "NHMW", "Naturhistorisches Museum Wien")
    csv = ("catalogNumber,scientificName,taxonRank,eventDate,country,collection\n"
           "NHMW-42,Curculio nucum,species,2024-05-20,Austria,Naturhistorisches Museum Wien\n")
    ds = _mk(session, content=csv)
    assert bi.progress(session, ds.id)["ready"] == 1
    bi.import_ready(session, ds.id)
    co = session.query(CollectionObject).filter_by(catalog_number="NHMW-42").one()
    assert co.repository_id == other.id                # the named collection, not home


def test_collection_by_code_also_matches(session):
    _repo(session, "JJPC", "Home", default=True)
    _repo(session, "NHMW", "Naturhistorisches Museum Wien")
    csv = ("catalogNumber,scientificName,taxonRank,eventDate,country,collection\n"
           "NHMW-9,Curculio nucum,species,2024-05-20,Austria,NHMW\n")   # code, not full name
    ds = _mk(session, content=csv)
    assert bi.progress(session, ds.id)["ready"] == 1


def test_unknown_collection_is_blocked_not_fabricated(session):
    _repo(session, "JJPC", "Home", default=True)
    csv = ("catalogNumber,scientificName,taxonRank,eventDate,country,collection\n"
           "X-1,Curculio nucum,species,2024-05-20,Austria,Museum of Nowhere\n")
    ds = _mk(session, content=csv)
    rec = session.query(ImportDatasetRecord).filter_by(import_dataset_id=ds.id).one()
    assert rec.status == "blocked"
    assert "not set up" in rec.error_message


def test_no_default_and_no_collection_column_blocks(session):
    # no default collection anywhere
    ds = _mk(session)
    assert bi.progress(session, ds.id)["blocked"] == 2


# ── import creates full records + is idempotent ─────────────────────────────

def test_import_creates_specimen_event_determination(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    counts = bi.import_ready(session, ds.id)
    assert counts["imported"] == 2
    co = session.query(CollectionObject).filter_by(catalog_number="JJPC-00001").one()
    assert co.collecting_event is not None
    assert co.collecting_event.country_obj.name == "Germany"
    det = co.determinations[0]
    assert det.taxon.scientific_name == "Otiorhynchus sulcatus"


def test_verbatim_range_date_is_parsed_to_interval(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    co = session.query(CollectionObject).filter_by(catalog_number="JJPC-00002").one()
    assert co.collecting_event.event_date == "2023-08-28/2023-08-30"


def test_reimport_does_not_duplicate(session):
    _repo(session, "JJPC", "Home", default=True)
    ds1 = _mk(session, name="First")
    bi.import_ready(session, ds1.id)
    n = session.query(CollectionObject).count()
    ds2 = _mk(session, name="Second")                 # same (repo, catalogNumber) pairs
    counts = bi.import_ready(session, ds2.id)
    assert counts["imported"] == 2                    # both recognised
    assert session.query(CollectionObject).count() == n   # but none created again


def test_record_links_the_created_specimen(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    rec = (session.query(ImportDatasetRecord)
           .filter_by(import_dataset_id=ds.id, row_index=0).one())
    assert rec.status == "imported"
    assert rec.collection_object_id is not None
    assert session.get(CollectionObject, rec.collection_object_id).catalog_number == "JJPC-00001"


def test_import_is_resumable(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    bi.import_ready(session, ds.id, max_records=1)
    assert bi.progress(session, ds.id)["imported"] == 1
    bi.import_ready(session, ds.id, max_records=50)
    assert bi.progress(session, ds.id)["imported"] == 2
    assert bi.progress(session, ds.id)["ready"] == 0


def test_shipped_template_stages_and_imports(session):
    """The downloadable CSV template must be correct: both worked rows stage ready and
    import cleanly (row 1 → home collection, row 2 → the named other collection with its
    verbatim range date parsed to an interval)."""
    _repo(session, "JJPC", "Jilg Private Collection", default=True)
    _repo(session, "NHMW", "Naturhistorisches Museum Wien")
    ds = _mk(session, content=bi.OCCURRENCE_TEMPLATE_CSV)
    counts = bi.progress(session, ds.id)
    assert counts["ready"] == 2 and counts["blocked"] == 0 and counts["errored"] == 0
    bi.import_ready(session, ds.id)
    assert bi.progress(session, ds.id)["imported"] == 2
    home = session.query(CollectionObject).filter_by(catalog_number="JJPC-00001").one()
    assert home.repository.collection_code == "JJPC"
    other = session.query(CollectionObject).filter_by(catalog_number="NHMW-00042").one()
    assert other.repository.collection_code == "NHMW"
    assert other.collecting_event.event_date == "2023-08-28/2023-08-30"


def test_delete_dataset_keeps_imported_specimens(session):
    _repo(session, "JJPC", "Home", default=True)
    ds = _mk(session)
    bi.import_ready(session, ds.id)
    n = session.query(CollectionObject).count()
    bi.delete_dataset(session, ds.id)
    assert session.get(ImportDataset, ds.id) is None
    assert session.query(CollectionObject).count() == n
