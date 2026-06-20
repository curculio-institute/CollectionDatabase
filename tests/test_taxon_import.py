"""Tests for taxon import merging: TW and POWO imports against manually-created taxa.

Scenarios covered:
  - A complete plant hierarchy built manually (species → order).
  - TW import of a second species in the same genus: ancestors reused, no duplicates.
  - TW import of the exact same species: no duplication, OTU id back-filled, authorship NOT
    overwritten.
  - TW import of a synonym of an existing accepted name: synonym linked to existing row.
  - TW import of a completely new synonym/accepted pair: both rows created.
  - TW import preserves tribe-level parent chain (TW knows tribe; genus parent unchanged).
  - POWO import of a second species: genus reused, but genus's parent CORRECTED from tribe
    to family (POWO has no tribe knowledge).
  - POWO import of the exact same species: no duplication, authorship back-filled.
  - POWO import of a synonym: synonym linked to accepted name.
  - Corrections list: entries appended when TW fixes a stale rank on an existing row.
"""
import pytest
from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import (
    create_taxon_direct,
    get_or_create_from_tw_data,
    get_or_create_from_powo_data,
)


# ---------------------------------------------------------------------------
# Helpers — build fake external-source dicts
# ---------------------------------------------------------------------------

def _tw_species(
    epithet: str,
    genus: str,
    family: str,
    order: str = "Asterales",
    tribe: str | None = None,
    authorship: str = "L.",
    otu_id: int | None = None,
    genus_otu_id: int | None = None,
    nomenclatural_code: str = "icn",
) -> tuple[dict, int | None]:
    """Return (tw_dict, otu_id) for a TW species record.

    The tw dict mimics what fetch_full_classification() returns after augmenting
    a raw TaxonWorks taxon_names record with ancestor fields.
    otu_id is passed separately to get_or_create_from_tw_data (not embedded).
    """
    d: dict = {
        "rank": "species",
        "name": epithet,
        "cached_author_year": authorship,
        "nomenclatural_code": nomenclatural_code,
        "genus": genus,
        "family": family,
        "taxon_order": order,
    }
    if tribe:
        d["tribe"] = tribe
    if genus_otu_id:
        d["genus_otu_id"] = genus_otu_id
    return d, otu_id


def _tw_synonym(
    epithet: str,
    genus: str,
    family: str,
    valid_tw_dict: dict,
    valid_otu_id: int,
    authorship: str = "Nutt.",
    otu_id: int | None = None,
    tribe: str | None = None,
) -> tuple[dict, int | None]:
    """Return (tw_dict, otu_id) for a TW synonym record (with _valid_tw_data embedded)."""
    d, _ = _tw_species(
        epithet=epithet, genus=genus, family=family, tribe=tribe, authorship=authorship,
    )
    d["_valid_tw_data"] = valid_tw_dict
    d["_valid_otu_id"] = valid_otu_id
    return d, otu_id


def _powo_species(
    scientific_name: str,
    genus: str,
    family: str,
    authorship: str = "L.",
    is_synonym: bool = False,
    nomenclatural_code: str = "ICN",
) -> dict:
    """Return a powo_fields dict as produced by powo.fields_from_powo()."""
    return {
        "scientific_name": scientific_name,
        "taxon_rank": "species",
        "scientific_name_authorship": authorship,
        "nomenclatural_code": nomenclatural_code,
        "family": family,
        "genus": genus,
        "ancestor_authorships": {},
        "is_synonym": is_synonym,
    }


def _powo_genus(
    scientific_name: str,
    family: str,
    authorship: str = "L.",
    nomenclatural_code: str = "ICN",
    ancestor_authorships: dict | None = None,
) -> dict:
    """Return a powo_fields dict for a genus picked directly from POWO."""
    return {
        "scientific_name": scientific_name,
        "taxon_rank": "genus",
        "scientific_name_authorship": authorship,
        "nomenclatural_code": nomenclatural_code,
        "family": family,
        "genus": None,
        "ancestor_authorships": ancestor_authorships or {},
        "is_synonym": False,
    }


# ---------------------------------------------------------------------------
# Fixture — full manually-created plant hierarchy
# ---------------------------------------------------------------------------

@pytest.fixture
def plant_tree(session):
    """
    Build a hand-crafted Asterales → Asteraceae → Anthemideae → Achillea →
    Achillea millefolium hierarchy using create_taxon_direct.

    This simulates a user who typed every rank manually before any external
    import was attempted.  Returns a dict keyed by rank label.
    """
    order = create_taxon_direct(
        session, scientific_name="Asterales", taxon_rank="order",
        nomenclatural_code="ICN",
    )
    family = create_taxon_direct(
        session, scientific_name="Asteraceae", taxon_rank="family",
        nomenclatural_code="ICN", parent_name_usage_id=order.id,
    )
    tribe = create_taxon_direct(
        session, scientific_name="Anthemideae", taxon_rank="tribe",
        nomenclatural_code="ICN", parent_name_usage_id=family.id,
    )
    genus = create_taxon_direct(
        session, scientific_name="Achillea", taxon_rank="genus",
        nomenclatural_code="ICN", parent_name_usage_id=tribe.id,
    )
    species = create_taxon_direct(
        session, scientific_name="Achillea millefolium", taxon_rank="species",
        nomenclatural_code="ICN", parent_name_usage_id=genus.id,
    )
    return {
        "order": order, "family": family, "tribe": tribe,
        "genus": genus, "species": species,
    }


# ---------------------------------------------------------------------------
# Manual tree sanity
# ---------------------------------------------------------------------------

def test_manual_tree_parent_chain(plant_tree, session):
    """All five manually-created rows exist and are linked correctly."""
    t = plant_tree
    assert t["species"].parent_name_usage_id == t["genus"].id
    assert t["genus"].parent_name_usage_id == t["tribe"].id
    assert t["tribe"].parent_name_usage_id == t["family"].id
    assert t["family"].parent_name_usage_id == t["order"].id
    assert t["order"].parent_name_usage_id is None

    count = session.query(Taxon).filter(
        Taxon.scientific_name.in_(["Asterales", "Asteraceae", "Anthemideae", "Achillea", "Achillea millefolium"])
    ).count()
    assert count == 5


# ---------------------------------------------------------------------------
# TaxonWorks imports
# ---------------------------------------------------------------------------

def test_tw_import_second_species_reuses_all_ancestors(plant_tree, session):
    """Importing a new TW species in the same genus creates exactly one new row."""
    tw_dict, otu_id = _tw_species(
        epithet="ptarmica", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", authorship="L.", otu_id=1001,
    )
    before = session.query(Taxon).count()
    result = get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id)
    after = session.query(Taxon).count()

    assert result.scientific_name == "Achillea ptarmica"
    assert after == before + 1  # only the new species row

    # Ancestor rows were reused — no duplicates.
    assert session.query(Taxon).filter(Taxon.scientific_name == "Achillea").count() == 1
    assert session.query(Taxon).filter(Taxon.scientific_name == "Asteraceae").count() == 1
    assert session.query(Taxon).filter(Taxon.scientific_name == "Anthemideae").count() == 1


def test_tw_import_existing_species_no_duplicate(plant_tree, session):
    """Importing a TW species that already exists locally produces no new row."""
    tw_dict, otu_id = _tw_species(
        epithet="millefolium", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", authorship="L.", otu_id=2001,
    )
    before = session.query(Taxon).count()
    result = get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id)
    after = session.query(Taxon).count()

    assert after == before  # no new rows
    assert result.id == plant_tree["species"].id  # same row returned
    assert result.taxonworks_otu_id == 2001  # OTU id back-filled


def test_tw_import_does_not_overwrite_existing_authorship(plant_tree, session):
    """TW import never overwrites an authorship that is already set."""
    # Give the existing species a local authorship.
    plant_tree["species"].scientific_name_authorship = "Local Author"
    session.flush()

    tw_dict, otu_id = _tw_species(
        epithet="millefolium", genus="Achillea", family="Asteraceae",
        authorship="L.", otu_id=2002,
    )
    get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id)
    session.refresh(plant_tree["species"])

    assert plant_tree["species"].scientific_name_authorship == "Local Author"


def test_tw_import_backfills_authorship_when_missing(plant_tree, session):
    """TW import fills in authorship on ancestor rows that have none."""
    assert plant_tree["genus"].scientific_name_authorship is None

    tw_dict, otu_id = _tw_species(
        epithet="ptarmica", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", authorship="L.", otu_id=1002,
    )
    # genus_authorship for parent-row back-fill
    tw_dict["genus_authorship"] = "Mill."
    get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id)
    session.refresh(plant_tree["genus"])

    assert plant_tree["genus"].scientific_name_authorship == "Mill."


def test_tw_import_synonym_of_existing_accepted(plant_tree, session):
    """TW synonym import creates one new synonym row linked to the existing accepted name."""
    valid_tw, _ = _tw_species(
        epithet="millefolium", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", authorship="L.", otu_id=2001,
    )
    syn_tw, syn_otu = _tw_synonym(
        epithet="lanulosa", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae",
        valid_tw_dict=valid_tw, valid_otu_id=2001, otu_id=3001,
    )

    before = session.query(Taxon).count()
    synonym = get_or_create_from_tw_data(session, syn_tw, otu_id=syn_otu)
    after = session.query(Taxon).count()

    assert after == before + 1  # only the synonym row is new
    assert synonym.scientific_name == "Achillea lanulosa"
    assert synonym.accepted_name_usage_id is not None
    assert synonym.accepted_name_usage_id == plant_tree["species"].id


def test_tw_import_synonym_both_new(session):
    """TW synonym import when neither accepted nor synonym exists yet creates both rows."""
    valid_tw, _ = _tw_species(
        epithet="ligustica", genus="Achillea", family="Asteraceae",
        authorship="L.", otu_id=4001,
    )
    syn_tw, syn_otu = _tw_synonym(
        epithet="crithmifolia", genus="Achillea", family="Asteraceae",
        valid_tw_dict=valid_tw, valid_otu_id=4001, otu_id=4002,
    )

    synonym = get_or_create_from_tw_data(session, syn_tw, otu_id=syn_otu)
    accepted = session.query(Taxon).filter_by(scientific_name="Achillea ligustica").first()

    assert accepted is not None
    assert accepted.accepted_name_usage_id is None
    assert synonym.accepted_name_usage_id is not None
    assert synonym.accepted_name_usage_id == accepted.id


def test_tw_import_cross_genus_synonym_shares_accepted_parent(session):
    """A cross-genus synonym must take its accepted name's parent (Inv1), not its
    own genus — otherwise the synonym-integrity trigger would reject the import."""
    valid_tw, _ = _tw_species(
        epithet="norici", genus="Otiorhynchus", family="Curculionidae",
        order="Coleoptera", authorship="Reitter", otu_id=5001, nomenclatural_code="iczn",
    )
    syn_tw, syn_otu = _tw_synonym(
        epithet="rubidus", genus="Curculio", family="Curculionidae",
        valid_tw_dict=valid_tw, valid_otu_id=5001, otu_id=5002,
    )
    syn_tw["nomenclatural_code"] = "iczn"
    synonym = get_or_create_from_tw_data(session, syn_tw, otu_id=syn_otu)   # must not raise
    accepted = session.query(Taxon).filter_by(scientific_name="Otiorhynchus norici").first()
    assert synonym.accepted_name_usage_id == accepted.id
    assert synonym.parent_name_usage_id == accepted.parent_name_usage_id   # shares accepted's parent
    curculio = session.query(Taxon).filter_by(scientific_name="Curculio", taxon_rank="genus").first()
    assert curculio is None or synonym.parent_name_usage_id != curculio.id


def test_tw_import_preserves_tribe_parent_chain(plant_tree, session):
    """TW provides tribe info, so the genus parent stays pointing at Anthemideae."""
    tribe_id_before = plant_tree["genus"].parent_name_usage_id

    tw_dict, otu_id = _tw_species(
        epithet="ptarmica", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", authorship="L.", otu_id=1003,
    )
    get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id)
    session.refresh(plant_tree["genus"])

    assert plant_tree["genus"].parent_name_usage_id == tribe_id_before


def test_tw_mismatch_reports_rank_conflict(session):
    """If an existing row has the wrong rank, a mismatch is reported but the rank is not changed.

    The row is still found via OTU ID and used as the genus parent for the new
    species — we don't silently correct local data, but we do flag the conflict.
    """
    wrong = create_taxon_direct(
        session, scientific_name="Achillea", taxon_rank="subgenus",
        taxonworks_otu_id=5001,
    )

    tw_dict, otu_id = _tw_species(
        epithet="ptarmica", genus="Achillea", family="Asteraceae", otu_id=1004,
        genus_otu_id=5001,
    )
    mismatches: list[str] = []
    get_or_create_from_tw_data(session, tw_dict, otu_id=otu_id, mismatches=mismatches)
    session.refresh(wrong)

    assert wrong.taxon_rank == "subgenus"  # NOT changed
    assert any("Achillea" in m and "genus" in m for m in mismatches)


# ---------------------------------------------------------------------------
# POWO imports
# ---------------------------------------------------------------------------

def test_powo_import_second_species_reuses_genus(plant_tree, session):
    """POWO import of a new species in an existing genus creates exactly one new row."""
    powo = _powo_species("Achillea ptarmica", genus="Achillea", family="Asteraceae")
    before = session.query(Taxon).count()
    result = get_or_create_from_powo_data(session, powo)
    after = session.query(Taxon).count()

    assert result.scientific_name == "Achillea ptarmica"
    assert after == before + 1
    assert session.query(Taxon).filter(Taxon.scientific_name == "Achillea").count() == 1


def test_powo_import_does_not_reparent_genus(plant_tree, session):
    """POWO has no tribe knowledge, so it would want to set the genus parent to the
    family directly.  Under the fill-NULL-only policy the existing tribe parent is
    preserved and a mismatch is reported instead.
    """
    assert plant_tree["genus"].parent_name_usage_id == plant_tree["tribe"].id

    powo = _powo_species("Achillea ptarmica", genus="Achillea", family="Asteraceae")
    mismatches: list[str] = []
    get_or_create_from_powo_data(session, powo, mismatches=mismatches)
    session.refresh(plant_tree["genus"])

    assert plant_tree["genus"].parent_name_usage_id == plant_tree["tribe"].id  # unchanged
    assert any("Achillea" in m and "parent" in m for m in mismatches)


def test_powo_import_existing_species_no_duplicate(plant_tree, session):
    """POWO import of a species already in the DB returns the existing row."""
    powo = _powo_species(
        "Achillea millefolium", genus="Achillea", family="Asteraceae", authorship="L.",
    )
    before = session.query(Taxon).count()
    result = get_or_create_from_powo_data(session, powo)
    after = session.query(Taxon).count()

    assert after == before
    assert result.id == plant_tree["species"].id


def test_powo_import_backfills_authorship(plant_tree, session):
    """POWO fills in authorship on existing rows that have none.

    The target taxon's authorship comes from `scientific_name_authorship`; the
    backfill onto the existing (NULL-authorship) species row uses that value.
    """
    assert plant_tree["species"].scientific_name_authorship is None

    powo = _powo_species(
        "Achillea millefolium", genus="Achillea", family="Asteraceae", authorship="L.",
    )
    # POWO classification includes the species itself as the last entry with author.
    powo["ancestor_authorships"] = {"species": "L."}

    get_or_create_from_powo_data(session, powo)
    session.refresh(plant_tree["species"])

    assert plant_tree["species"].scientific_name_authorship == "L."


def test_powo_import_genus_directly_keeps_authorship(session):
    """A genus imported directly from POWO retains its OWN authorship.

    Regression: the ancestor-authorship loop reused the `auth` variable, so the
    target genus was created with the loop's final value — ancestor_authorships
    ['species'], which is absent for a genus record → None. A genus record's
    classification carries family/genus authors but no species entry.
    """
    powo = _powo_genus(
        "Achillea", family="Asteraceae", authorship="L.",
        ancestor_authorships={"family": "Bercht. & J.Presl", "genus": "L."},
    )
    result = get_or_create_from_powo_data(session, powo)

    assert result.taxon_rank == "genus"
    assert result.scientific_name == "Achillea"
    assert result.scientific_name_authorship == "L."   # was None before the fix


def test_powo_import_species_keeps_own_authorship_without_species_classification(session):
    """A directly-imported species keeps its own authorship even when the
    classification map has no 'species' entry (guards the same clobber bug for
    the species/infraspecific case)."""
    powo = _powo_species(
        "Achillea distans", genus="Achillea", family="Asteraceae", authorship="Waldst. & Kit.",
    )
    powo["ancestor_authorships"] = {"family": "Bercht. & J.Presl", "genus": "L."}
    result = get_or_create_from_powo_data(session, powo)

    assert result.taxon_rank == "species"
    assert result.scientific_name_authorship == "Waldst. & Kit."   # was None before the fix


def test_powo_import_does_not_overwrite_existing_authorship(plant_tree, session):
    """POWO never overwrites an authorship already present."""
    plant_tree["species"].scientific_name_authorship = "Local Author"
    session.flush()

    powo = _powo_species(
        "Achillea millefolium", genus="Achillea", family="Asteraceae", authorship="L.",
    )
    get_or_create_from_powo_data(session, powo)
    session.refresh(plant_tree["species"])

    assert plant_tree["species"].scientific_name_authorship == "Local Author"


def test_powo_import_synonym_linked_to_existing_accepted(plant_tree, session):
    """POWO synonym import creates the synonym row and links it to the existing accepted name."""
    accepted_fields = _powo_species(
        "Achillea millefolium", genus="Achillea", family="Asteraceae",
    )
    synonym_fields = _powo_species(
        "Achillea lanulosa", genus="Achillea", family="Asteraceae",
        authorship="Nutt.", is_synonym=True,
    )

    before = session.query(Taxon).count()
    synonym = get_or_create_from_powo_data(session, synonym_fields, accepted_fields=accepted_fields)
    after = session.query(Taxon).count()

    assert after == before + 1  # only the synonym row is new
    assert synonym.accepted_name_usage_id is not None
    assert synonym.accepted_name_usage_id == plant_tree["species"].id


def test_powo_import_synonym_both_new(session):
    """POWO synonym import when neither name exists yet creates both rows."""
    accepted_fields = _powo_species(
        "Achillea ligustica", genus="Achillea", family="Asteraceae",
    )
    synonym_fields = _powo_species(
        "Achillea crithmifolia", genus="Achillea", family="Asteraceae",
        authorship="Waldst. & Kit.", is_synonym=True,
    )

    synonym = get_or_create_from_powo_data(session, synonym_fields, accepted_fields=accepted_fields)
    accepted = session.query(Taxon).filter_by(scientific_name="Achillea ligustica").first()

    assert accepted is not None and accepted.accepted_name_usage_id is None
    assert synonym.accepted_name_usage_id is not None
    assert synonym.accepted_name_usage_id == accepted.id
