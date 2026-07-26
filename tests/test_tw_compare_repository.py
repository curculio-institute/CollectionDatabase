"""`compare_repository` against real specimens — the DB half of #149 Step 1.

`tests/test_tw_compare_index.py` covers the pure index-building functions; this module
covers what they are *for*: which of the two TaxonWorks sources answers which question.

The distinction is the whole point, and it is a data-safety one (CLAUDE.md §5c):

- **Existence** comes from the `/identifiers` index and is **never namespace-scoped**.
  A specimen TaxonWorks files under someone else's namespace is still on TaxonWorks, and
  identifier uniqueness is *per namespace* — so calling it absent re-uploads it into a
  CREATE-ONLY importer and TaxonWorks accepts the duplicate silently.
- **Field values** come from `dwc_occurrences`, a generated, cached projection that can
  lag a fresh import. When the index says a specimen is there but the projection has no
  row for it, that is `on_tw_not_compared` — never `not_on_tw` (which would upload it
  twice) and never `synced` (nothing was compared).
- **The orphan sweep** *is* namespace-scoped, or every other collection in the project
  counts as this one's orphan.
"""
import app.services.specimens as spec_svc
from app.services.tw_compare import build_catalog_index, compare_repository
from tests.helpers import ensure_repo

_TYPE = "Identifier::Local::CatalogNumber"


def _index(*rows: tuple[str, int, str]):
    """A `CatalogIndex` from `(catalog_number, tw_object_id, namespace_short_name)`.

    Built through the real `/identifiers` row shape rather than by constructing
    `TwCatalogEntry` directly, so the reducer stays on the tested path: TaxonWorks
    **splits** the catalog number — `identifier` is the bare part, `cached` the rendered
    full form, and `cached` is the join key (matching `identifier` scored 0 overlap on
    all 39 real specimens).
    """
    return build_catalog_index([
        {
            "type": _TYPE,
            "identifier_object_type": "CollectionObject",
            "cached": cat,
            "identifier": cat.split("-", 1)[-1],
            "identifier_object_id": obj_id,
            "namespace_id": 6057,
            "namespace": {"short_name": ns},
        }
        for cat, obj_id, ns in rows
    ])


def _specimen(session, catalog_number: str, code: str = "JJPC", **kw):
    repo_id = ensure_repo(session, code)
    co = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number=catalog_number,
        repository_id=repo_id, **kw,
    )
    session.flush()
    return co


def _repo_id(session, code: str = "JJPC") -> int:
    return ensure_repo(session, code)


def _occurrence(cat: str, obj_id: int, **fields) -> dict:
    row = {"catalogNumber": cat, "dwc_occurrence_object_id": obj_id,
           "dwc_occurrence_object_type": "CollectionObject"}
    row.update(fields)
    return row


# ── Existence is the index's answer, not the projection's ───────────────────────

def test_on_the_index_but_not_in_the_projection_is_not_reported_as_missing(session):
    """The lag case. Reporting it as `not_on_tw` would put the specimen back into the
    export file and mint a duplicate TaxonWorks' API cannot delete."""
    _specimen(session, "JJPC-00001")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00001", 501, "JJPC")),
    )
    assert result.on_tw_not_compared == ("JJPC-00001",)
    assert result.not_on_tw == ()
    assert result.synced == ()


def test_absent_from_the_index_is_not_yet_uploaded(session):
    _specimen(session, "JJPC-00002")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session), index=_index(),
    )
    assert result.not_on_tw == ("JJPC-00002",)
    assert result.on_tw_not_compared == ()


def test_on_the_index_and_matching_the_projection_is_synced(session):
    co = _specimen(session, "JJPC-00003", individual_count=1)
    result = compare_repository(
        session,
        {"JJPC-00003": [_occurrence("JJPC-00003", 503, individualCount="1",
                                    basisOfRecord=co.basis_of_record or "")]},
        repository_id=_repo_id(session),
        index=_index(("JJPC-00003", 503, "JJPC")),
    )
    assert result.synced == ("JJPC-00003",)
    assert result.diverged == ()


def test_a_field_difference_is_reported_with_its_taxonworks_id(session):
    _specimen(session, "JJPC-00004", individual_count=1)
    result = compare_repository(
        session,
        {"JJPC-00004": [_occurrence("JJPC-00004", 504, individualCount="7")]},
        repository_id=_repo_id(session),
        index=_index(("JJPC-00004", 504, "JJPC")),
    )
    assert [d.catalog_number for d in result.diverged] == ["JJPC-00004"]
    assert result.diverged[0].tw_object_id == 504
    assert any("individualCount" in fd for fd in result.diverged[0].field_diffs)


def test_existence_ignores_the_namespace(session):
    """Filed under a foreign namespace on TaxonWorks — still on TaxonWorks. Uniqueness
    is per namespace, so re-uploading it would be accepted without complaint."""
    _specimen(session, "JJPC-00005")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00005", 505, "OTHER")),
    )
    assert result.not_on_tw == ()
    assert result.on_tw_not_compared == ("JJPC-00005",)


def test_a_foreign_namespace_on_a_local_specimen_is_a_collection_mismatch(session):
    _specimen(session, "JJPC-00006")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00006", 506, "OTHER")),
    )
    assert [(m.catalog_number, m.tw_namespace, m.local_collection)
            for m in result.collection_mismatch] == [("JJPC-00006", "OTHER", "JJPC")]


def test_a_matching_namespace_is_not_a_mismatch(session):
    _specimen(session, "JJPC-00007")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00007", 507, "JJPC")),
    )
    assert result.collection_mismatch == ()


# ── The orphan direction — on TaxonWorks, not held here ─────────────────────────

def test_a_catalog_number_nowhere_in_the_local_database_is_orphaned(session):
    _specimen(session, "JJPC-00010")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00010", 510, "JJPC"), ("JJPC-09999", 599, "JJPC")),
    )
    assert [(o.catalog_number, o.local_collection) for o in result.orphaned] \
        == [("JJPC-09999", None)]
    assert result.moved == ()


def test_a_re_homed_specimen_reads_as_moved_not_as_deleted(session):
    """A re-home keeps `catalog_number` and only re-points `repository_id`, so from
    TaxonWorks' side a move and a deletion look identical. Calling a move a deletion
    would be the silent wrong value of CLAUDE.md §2 — the DB-wide lookup separates them.
    """
    _specimen(session, "JJPC-00011")
    _specimen(session, "JJPC-00012", code="LOAN")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00011", 511, "JJPC"), ("JJPC-00012", 512, "JJPC")),
    )
    assert [(m.catalog_number, m.local_collection) for m in result.moved] \
        == [("JJPC-00012", "LOAN")]
    assert result.orphaned == ()


def test_another_namespaces_records_are_not_our_orphans(session):
    _specimen(session, "JJPC-00013")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00013", 513, "JJPC"), ("XXXX-00001", 590, "XXXX")),
    )
    assert result.orphaned == () and result.moved == ()


def test_without_an_index_the_orphan_direction_is_declared_uncompared(session):
    """A report that silently omits a whole class reads as complete — same reason
    `media_not_compared` is declared rather than left implicit."""
    _specimen(session, "JJPC-00014")
    result = compare_repository(session, {}, repository_id=_repo_id(session))
    assert result.orphans_not_compared is True
    assert result.orphaned == () and result.moved == ()


def test_with_an_index_the_orphan_direction_is_compared(session):
    _specimen(session, "JJPC-00015")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00015", 515, "JJPC")),
    )
    assert result.orphans_not_compared is False


# ── Duplicates and withheld-but-present ─────────────────────────────────────────

def test_a_duplicate_catalog_number_comes_from_the_index(session):
    """#149 step 1.3 — reported with each record's own namespace, never silently
    picking one. Taken from the index because it is namespace-aware and authoritative
    where the projection may not list the row at all."""
    _specimen(session, "JJPC-00020")
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00020", 520, "JJPC"), ("JJPC-00020", 521, "OTHER")),
    )
    assert [(d.catalog_number, d.tw_object_ids, d.institution_codes)
            for d in result.duplicates] \
        == [("JJPC-00020", (520, 521), ("JJPC", "OTHER"))]


def test_a_confidential_specimen_on_taxonworks_is_reported_as_leaked(session):
    """#149 step 1.4. The TaxonWorks id comes from the index when the projection has no
    row — otherwise a withheld specimen would be found but have no link to act on."""
    _specimen(session, "JJPC-00021", confidential=1)
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00021", 521, "JJPC")),
    )
    assert [(lk.catalog_number, lk.tw_object_id) for lk in result.leaked] \
        == [("JJPC-00021", 521)]
    assert result.ineligible_count == 1 and result.eligible_count == 0


def test_a_confidential_specimen_absent_from_taxonworks_is_not_leaked(session):
    _specimen(session, "JJPC-00022", confidential=1)
    result = compare_repository(
        session, {}, repository_id=_repo_id(session), index=_index(),
    )
    assert result.leaked == ()
    assert result.ineligible_count == 1
