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
from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import compose_scientific_name
from app.services.tw_compare import (
    build_catalog_index, compare_repository, eligible_specimens, ineligible_specimens,
)
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


def _species(session):
    """One shared, definite species determination target.

    Every specimen here gets one, because since 2026-07-26 export eligibility also
    requires a certain identification (`dwc_export._determination_reasons`) — without it
    each fixture would be ineligible for a reason these tests are not about, and the
    existence/orphan assertions would never be reached.
    """
    sp = session.query(Taxon).filter_by(scientific_name="Otiorhynchus crypticus").first()
    if sp is not None:
        return sp
    genus = Taxon(name_element="Otiorhynchus", scientific_name="Otiorhynchus",
                  taxon_rank="genus", nomenclatural_code="ICZN",
                  created_at=_utcnow(), updated_at=_utcnow())
    session.add(genus)
    session.flush()
    sp = Taxon(name_element="crypticus", scientific_name="crypticus",
               taxon_rank="species", parent_name_usage_id=genus.id,
               nomenclatural_code="ICZN", created_at=_utcnow(), updated_at=_utcnow())
    session.add(sp)
    session.flush()
    sp.scientific_name = compose_scientific_name(session, sp)
    session.flush()
    return sp


def _specimen(session, catalog_number: str, code: str = "JJPC", **kw):
    repo_id = ensure_repo(session, code)
    co = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number=catalog_number,
        repository_id=repo_id, **kw,
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=co.id, taxon_id=_species(session).id,
        is_current=1,
    )
    session.flush()
    session.refresh(co)
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
        {"JJPC-00003": [_occurrence(
            "JJPC-00003", 503, individualCount="1",
            basisOfRecord=co.basis_of_record or "",
            scientificName="Otiorhynchus crypticus", taxonRank="species",
        )]},
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


def test_a_leaked_specimen_also_reports_its_field_diffs(session):
    """Live review: 'JJPC-00010 slipped through because the collector's name was added
    later' — the leak already says the record needs fixing on TaxonWorks, but not
    whether anything ELSE in it is also stale. `field_diffs` (the same computation
    `diverged` runs for eligible specimens) must run for a leaked one too, so the
    report shows both problems in one place instead of a second discovery pass."""
    co = _specimen(session, "JJPC-00025", confidential=1, individual_count=1)
    result = compare_repository(
        session,
        {"JJPC-00025": [_occurrence("JJPC-00025", 525, individualCount="9")]},
        repository_id=_repo_id(session),
        index=_index(("JJPC-00025", 525, "JJPC")),
    )
    assert [lk.catalog_number for lk in result.leaked] == ["JJPC-00025"]
    assert any("individualCount" in fd for fd in result.leaked[0].field_diffs)


def test_a_leaked_specimen_with_no_projection_row_reports_no_field_diffs(session):
    """Same lag case `on_tw_not_compared` covers for eligible specimens — the index
    says TaxonWorks has it, but `dwc_occurrences` hasn't caught up, so there is
    nothing to diff against. Empty must mean 'not compared', never 'nothing differs'."""
    _specimen(session, "JJPC-00026", confidential=1)
    result = compare_repository(
        session, {}, repository_id=_repo_id(session),
        index=_index(("JJPC-00026", 526, "JJPC")),
    )
    assert [lk.catalog_number for lk in result.leaked] == ["JJPC-00026"]
    assert result.leaked[0].field_diffs == ()


def test_leaked_privacy_and_leaked_curation_partition_leaked(session):
    """Code review fix (#170 follow-up): `CompareResult.leaked_privacy`/
    `leaked_curation` are now the single source of truth `tw_sync_tab.py` reads
    (replacing three separately hand-written `[lk for lk in result.leaked if
    lk.privacy]` comprehensions) — they must actually partition `leaked` correctly,
    every row exactly once, on either side."""
    _specimen(session, "JJPC-00023", confidential=1)          # privacy
    repo_id = _repo_id(session)
    co = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-00024",
        repository_id=repo_id,
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=co.id, taxon_id=_species(session).id,
        is_current=1, identification_qualifier="cf.",              # curatorial
    )
    session.flush()

    result = compare_repository(
        session, {}, repository_id=repo_id,
        index=_index(("JJPC-00023", 523, "JJPC"), ("JJPC-00024", 524, "JJPC")),
    )
    assert {lk.catalog_number for lk in result.leaked} == {"JJPC-00023", "JJPC-00024"}
    assert [lk.catalog_number for lk in result.leaked_privacy] == ["JJPC-00023"]
    assert [lk.catalog_number for lk in result.leaked_curation] == ["JJPC-00024"]
    # A true partition: every leaked row on exactly one side, none on both, none lost.
    assert (set(result.leaked_privacy) | set(result.leaked_curation)) == set(result.leaked)
    assert not (set(result.leaked_privacy) & set(result.leaked_curation))


def test_scope_taxon_ids_restricts_which_local_specimens_are_counted(session):
    """Design pass (live review): the optional taxon restriction the Export step
    already applies must narrow this function's own specimen set too, so every count
    it reports (not_on_tw included) matches "restricted to this taxon", not the whole
    collection."""
    in_scope = _specimen(session, "JJPC-00030")   # Otiorhynchus crypticus, via _species()
    other_genus = Taxon(name_element="Curculio", scientific_name="Curculio",
                         taxon_rank="genus", nomenclatural_code="ICZN",
                         created_at=_utcnow(), updated_at=_utcnow())
    session.add(other_genus)
    session.flush()
    other_species = Taxon(name_element="nucum", scientific_name="nucum",
                           taxon_rank="species", parent_name_usage_id=other_genus.id,
                           nomenclatural_code="ICZN", created_at=_utcnow(),
                           updated_at=_utcnow())
    session.add(other_species)
    session.flush()
    other_species.scientific_name = compose_scientific_name(session, other_species)
    session.flush()
    out_of_scope = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-00031",
        repository_id=_repo_id(session),
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=out_of_scope.id, taxon_id=other_species.id,
        is_current=1,
    )
    session.flush()

    unscoped = compare_repository(
        session, {}, repository_id=_repo_id(session), index=_index(),
    )
    assert set(unscoped.not_on_tw) == {"JJPC-00030", "JJPC-00031"}

    in_scope_taxon_id = (
        session.query(Taxon).filter_by(scientific_name="Otiorhynchus crypticus")
        .first().id
    )
    scoped = compare_repository(
        session, {}, repository_id=_repo_id(session), index=_index(),
        scope_taxon_ids={in_scope_taxon_id},
    )
    assert scoped.not_on_tw == ("JJPC-00030",)


def test_eligible_and_ineligible_specimens_respect_scope_taxon_ids(session):
    """Code review fix: these two used to ignore `scope_taxon_ids` entirely, so a
    taxon-restricted Collections report showed a scoped count next to an unscoped
    detail list/Explore hand-off for the very same number — clicking either surfaced
    specimens outside the taxon the box itself claimed to represent."""
    in_scope_eligible = _specimen(session, "JJPC-00050")   # Otiorhynchus crypticus

    in_scope_taxon_id = (
        session.query(Taxon).filter_by(scientific_name="Otiorhynchus crypticus")
        .first().id
    )
    repo_id = _repo_id(session)
    in_scope_ineligible = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-00051",
        repository_id=repo_id,
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=in_scope_ineligible.id,
        taxon_id=in_scope_taxon_id, is_current=1,
        identification_qualifier="cf.",   # expresses doubt -> ineligible
    )

    genus = Taxon(name_element="Curculio", scientific_name="Curculio",
                  taxon_rank="genus", nomenclatural_code="ICZN",
                  created_at=_utcnow(), updated_at=_utcnow())
    session.add(genus)
    session.flush()
    other_species = Taxon(name_element="nucum", scientific_name="nucum",
                           taxon_rank="species", parent_name_usage_id=genus.id,
                           nomenclatural_code="ICZN", created_at=_utcnow(),
                           updated_at=_utcnow())
    session.add(other_species)
    session.flush()
    other_species.scientific_name = compose_scientific_name(session, other_species)
    session.flush()
    out_of_scope_eligible = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-00052",
        repository_id=repo_id,
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=out_of_scope_eligible.id,
        taxon_id=other_species.id, is_current=1,
    )
    session.flush()

    assert set(eligible_specimens(session, repository_id=repo_id)) == \
        {"JJPC-00050", "JJPC-00052"}
    assert {cat for cat, _ in ineligible_specimens(session, repository_id=repo_id)} == \
        {"JJPC-00051"}

    scoped_eligible = eligible_specimens(
        session, repository_id=repo_id, scope_taxon_ids={in_scope_taxon_id})
    assert scoped_eligible == ("JJPC-00050",)

    scoped_ineligible = ineligible_specimens(
        session, repository_id=repo_id, scope_taxon_ids={in_scope_taxon_id})
    assert [cat for cat, _ in scoped_ineligible] == ["JJPC-00051"]


def test_scope_excluded_specimen_on_taxonworks_is_not_reported_as_moved(session):
    """Live review bug: a specimen still held in THIS collection, on TaxonWorks under
    our namespace, but outside the current taxon scope, was reported as `moved` —
    "held in another local collection" — which was a false claim (elsewhere.get()
    found it in *our own* repository, not a different one). It must land in
    `excluded_by_scope` instead, and `moved` must stay reserved for a genuine
    cross-repository re-home."""
    genus = Taxon(name_element="Curculio", scientific_name="Curculio",
                  taxon_rank="genus", nomenclatural_code="ICZN",
                  created_at=_utcnow(), updated_at=_utcnow())
    session.add(genus)
    session.flush()
    excluded_species = Taxon(name_element="nucum", scientific_name="nucum",
                              taxon_rank="species", parent_name_usage_id=genus.id,
                              nomenclatural_code="ICZN", created_at=_utcnow(),
                              updated_at=_utcnow())
    session.add(excluded_species)
    session.flush()
    excluded_species.scientific_name = compose_scientific_name(session, excluded_species)
    session.flush()

    repo_id = _repo_id(session)
    co = spec_svc.create_collection_object(
        session, collecting_event_id=None, catalog_number="JJPC-00040",
        repository_id=repo_id,
    )
    session.flush()
    spec_svc.create_determination(
        session, collection_object_id=co.id, taxon_id=excluded_species.id,
        is_current=1,
    )
    session.flush()

    # Scope to the OTHER species (_species()) — "JJPC-00040" (Curculio nucum) falls
    # outside it, exactly the exclusion scenario.
    in_scope_taxon_id = _species(session).id
    result = compare_repository(
        session, {}, repository_id=repo_id,
        index=_index(("JJPC-00040", 540, "JJPC")),
        scope_taxon_ids={in_scope_taxon_id},
    )
    assert [(r.catalog_number, r.local_collection) for r in result.excluded_by_scope] \
        == [("JJPC-00040", "JJPC")]
    assert result.moved == ()
    assert result.orphaned == ()
