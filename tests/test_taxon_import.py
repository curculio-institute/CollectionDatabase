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
    get_or_create_from_wcvp_data,
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


def _wcvp_species(
    scientific_name: str,
    genus: str,
    family: str,
    authorship: str = "L.",
    genus_authorship: str | None = None,
    accepted: dict | None = None,
) -> dict:
    """A fields dict as produced by wcvp.fields_from_wcvp() for a species."""
    return {
        "scientific_name": scientific_name,
        "taxon_rank": "species",
        "scientific_name_authorship": authorship,
        "nomenclatural_code": "ICN",
        "ipni_id": None,
        "family": family,
        "genus": genus,
        "genus_authorship": genus_authorship,
        "species_name": None,
        "is_synonym": accepted is not None,
        "accepted": accepted,
    }


def _wcvp_genus(scientific_name: str, family: str, authorship: str = "L.") -> dict:
    """A fields dict for a genus picked directly from WCVP."""
    return {
        "scientific_name": scientific_name,
        "taxon_rank": "genus",
        "scientific_name_authorship": authorship,
        "nomenclatural_code": "ICN",
        "ipni_id": None,
        "family": family,
        "genus": None,
        "genus_authorship": None,
        "species_name": None,
        "is_synonym": False,
        "accepted": None,
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


def test_tw_import_cross_genus_synonym_keeps_own_genus(session):
    """Atomic model (Epic #30): a cross-genus synonym keeps its OWN-lineage parent
    (its original genus, which TW supplies) and composes to its own name; only the
    accepted link points at the valid name in the other genus."""
    valid_tw, _ = _tw_species(
        epithet="norici", genus="Otiorhynchus", family="Curculionidae",
        order="Coleoptera", authorship="Reitter", otu_id=5001, nomenclatural_code="iczn",
    )
    syn_tw, syn_otu = _tw_synonym(
        epithet="rubidus", genus="Curculio", family="Curculionidae",
        valid_tw_dict=valid_tw, valid_otu_id=5001, otu_id=5002,
    )
    syn_tw["nomenclatural_code"] = "iczn"
    synonym = get_or_create_from_tw_data(session, syn_tw, otu_id=syn_otu)
    accepted = session.query(Taxon).filter_by(scientific_name="Otiorhynchus norici").first()
    curculio = session.query(Taxon).filter_by(scientific_name="Curculio", taxon_rank="genus").first()
    assert synonym.accepted_name_usage_id == accepted.id
    assert curculio is not None
    assert synonym.parent_name_usage_id == curculio.id        # own genus, not accepted's
    assert synonym.scientific_name == "Curculio rubidus"
    assert synonym.name_element == "rubidus"


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
# Atomic name_element population (Epic #30, Phase 3)
# ---------------------------------------------------------------------------

def test_tw_import_populates_name_element(plant_tree, session):
    """Species → name_element = epithet; ancestor genus → its uninomial; the
    composed scientific_name is rebuilt from the chain."""
    tw, otu = _tw_species(
        epithet="ptarmica", genus="Achillea", family="Asteraceae",
        tribe="Anthemideae", otu_id=7001,
    )
    sp = get_or_create_from_tw_data(session, tw, otu_id=otu)
    assert sp.name_element == "ptarmica"
    assert sp.scientific_name == "Achillea ptarmica"
    genus = session.query(Taxon).filter_by(scientific_name="Achillea", taxon_rank="genus").first()
    assert genus.name_element == "Achillea"


def test_tw_import_species_with_subgenus_composes(session):
    """A species under a subgenus composes to 'Genus (Subgenus) epithet', and the
    subgenus ancestor row is stored fully composed as 'Genus (Subgenus)'."""
    tw = {
        "rank": "species", "name": "crypticus", "nomenclatural_code": "iczn",
        "cached_author_year": "Reitter",
        "genus": "Otiorhynchus", "subgenus": "Nihus",
        "family": "Curculionidae", "taxon_order": "Coleoptera",
    }
    sp = get_or_create_from_tw_data(session, tw, otu_id=8001)
    assert sp.name_element == "crypticus"
    assert sp.scientific_name == "Otiorhynchus (Nihus) crypticus"
    subg = session.query(Taxon).filter_by(taxon_rank="subgenus").first()
    assert subg.name_element == "Nihus"
    assert subg.scientific_name == "Otiorhynchus (Nihus)"


def test_tw_import_subspecies_composes(session):
    """A subspecies composes to a bare ICZN trinomial; its species parent row is
    created with the epithet as its element."""
    tw = {
        "rank": "subspecies", "name": "alpina", "nomenclatural_code": "iczn",
        "genus": "Achillea", "specific_epithet": "millefolium",
        "species_name": "Achillea millefolium", "family": "Asteraceae",
    }
    ssp = get_or_create_from_tw_data(session, tw, otu_id=9001)
    assert ssp.name_element == "alpina"
    assert ssp.scientific_name == "Achillea millefolium alpina"
    sp = session.query(Taxon).filter_by(taxon_rank="species").first()
    assert sp.name_element == "millefolium"
    assert sp.scientific_name == "Achillea millefolium"


def test_create_taxon_direct_derives_element_from_full_name(session):
    genus = create_taxon_direct(
        session, scientific_name="Achillea", taxon_rank="genus", nomenclatural_code="ICN",
    )
    sp = create_taxon_direct(
        session, scientific_name="Achillea nobilis", taxon_rank="species",
        nomenclatural_code="ICN", parent_name_usage_id=genus.id,
    )
    assert genus.name_element == "Achillea"
    assert sp.name_element == "nobilis"
    assert sp.scientific_name == "Achillea nobilis"


def test_create_taxon_direct_accepts_explicit_element(session):
    genus = create_taxon_direct(
        session, name_element="Otiorhynchus", taxon_rank="genus", nomenclatural_code="ICZN",
    )
    sp = create_taxon_direct(
        session, name_element="crypticus", taxon_rank="species",
        nomenclatural_code="ICZN", parent_name_usage_id=genus.id,
    )
    assert sp.name_element == "crypticus"
    assert sp.scientific_name == "Otiorhynchus crypticus"


# ---------------------------------------------------------------------------
# WCVP imports
#
# Ported from the POWO tests when POWO was replaced (#98). The two POWO-only regressions
# about the `ancestor_authorships` clobber are gone with the loop that caused them; the
# behaviour they protected — a directly-imported taxon keeps its OWN authorship — is still
# asserted below.
# ---------------------------------------------------------------------------

def test_wcvp_import_second_species_reuses_genus(plant_tree, session):
    """A new species in an existing genus creates exactly one new row."""
    fields = _wcvp_species("Achillea ptarmica", genus="Achillea", family="Asteraceae")
    before = session.query(Taxon).count()
    result = get_or_create_from_wcvp_data(session, fields)
    after = session.query(Taxon).count()

    assert result.scientific_name == "Achillea ptarmica"
    assert after == before + 1
    assert session.query(Taxon).filter(Taxon.scientific_name == "Achillea").count() == 1


def test_wcvp_import_does_not_reparent_genus(plant_tree, session):
    """WCVP has no rank between family and genus, so it would want to parent the genus on
    the family directly. Under the fill-NULL-only policy the existing tribe parent is
    preserved and a mismatch is reported instead — the local DB is the source of truth.
    """
    assert plant_tree["genus"].parent_name_usage_id == plant_tree["tribe"].id

    fields = _wcvp_species("Achillea ptarmica", genus="Achillea", family="Asteraceae")
    mismatches: list[str] = []
    get_or_create_from_wcvp_data(session, fields, mismatches=mismatches)
    session.refresh(plant_tree["genus"])

    assert plant_tree["genus"].parent_name_usage_id == plant_tree["tribe"].id  # unchanged
    assert any("Achillea" in m and "parent" in m for m in mismatches)


def test_wcvp_import_existing_species_no_duplicate(plant_tree, session):
    fields = _wcvp_species("Achillea millefolium", genus="Achillea", family="Asteraceae")
    before = session.query(Taxon).count()
    result = get_or_create_from_wcvp_data(session, fields)
    after = session.query(Taxon).count()

    assert after == before
    assert result.id == plant_tree["species"].id


def test_wcvp_import_backfills_authorship(plant_tree, session):
    """Fills authorship on an existing row that has none."""
    assert plant_tree["species"].scientific_name_authorship is None

    fields = _wcvp_species("Achillea millefolium", genus="Achillea", family="Asteraceae",
                           authorship="L.")
    get_or_create_from_wcvp_data(session, fields)
    session.refresh(plant_tree["species"])

    assert plant_tree["species"].scientific_name_authorship == "L."


def test_wcvp_import_genus_directly_keeps_its_own_authorship(session):
    result = get_or_create_from_wcvp_data(
        session, _wcvp_genus("Achillea", family="Asteraceae", authorship="L.")
    )
    assert result.taxon_rank == "genus"
    assert result.scientific_name == "Achillea"
    assert result.scientific_name_authorship == "L."


def test_wcvp_import_species_keeps_its_own_authorship(session):
    """The target's authorship is never overwritten by an ancestor's."""
    fields = _wcvp_species("Achillea distans", genus="Achillea", family="Asteraceae",
                           authorship="Waldst. & Kit.", genus_authorship="L.")
    result = get_or_create_from_wcvp_data(session, fields)

    assert result.scientific_name_authorship == "Waldst. & Kit."
    genus = session.get(Taxon, result.parent_name_usage_id)
    assert genus.scientific_name_authorship == "L."


def test_wcvp_import_does_not_overwrite_existing_authorship(plant_tree, session):
    plant_tree["species"].scientific_name_authorship = "Local Author"
    session.flush()

    fields = _wcvp_species("Achillea millefolium", genus="Achillea", family="Asteraceae",
                           authorship="L.")
    get_or_create_from_wcvp_data(session, fields)
    session.refresh(plant_tree["species"])

    assert plant_tree["species"].scientific_name_authorship == "Local Author"


def test_wcvp_import_synonym_linked_to_existing_accepted(plant_tree, session):
    accepted = _wcvp_species("Achillea millefolium", genus="Achillea", family="Asteraceae")
    synonym_fields = _wcvp_species(
        "Achillea lanulosa", genus="Achillea", family="Asteraceae",
        authorship="Nutt.", accepted=accepted,
    )

    before = session.query(Taxon).count()
    synonym = get_or_create_from_wcvp_data(session, synonym_fields)
    after = session.query(Taxon).count()

    assert after == before + 1  # only the synonym row is new
    assert synonym.accepted_name_usage_id == plant_tree["species"].id


def test_wcvp_import_sets_name_element(session):
    """The full name is split into the atomic element; composition rebuilds the same string."""
    fields = _wcvp_species("Achillea distans", genus="Achillea", family="Asteraceae",
                           authorship="Waldst. & Kit.")
    sp = get_or_create_from_wcvp_data(session, fields)
    assert sp.name_element == "distans"
    assert sp.scientific_name == "Achillea distans"


def test_wcvp_import_synonym_both_new(session):
    accepted = _wcvp_species("Achillea ligustica", genus="Achillea", family="Asteraceae")
    synonym_fields = _wcvp_species(
        "Achillea crithmifolia", genus="Achillea", family="Asteraceae",
        authorship="Waldst. & Kit.", accepted=accepted,
    )

    synonym = get_or_create_from_wcvp_data(session, synonym_fields)
    acc_row = session.query(Taxon).filter_by(scientific_name="Achillea ligustica").first()

    assert acc_row is not None and acc_row.accepted_name_usage_id is None
    assert synonym.accepted_name_usage_id == acc_row.id


def test_wcvp_import_synonym_in_another_genus_keeps_its_own_lineage(session):
    """Epic #30: the synonym stays under its own genus, so it is never renamed into — and
    merged with — its accepted name. See docs/plant_names.md §5."""
    accepted = _wcvp_species("Cytisus scoparius", genus="Cytisus", family="Fabaceae",
                             authorship="(L.) Link")
    synonym_fields = _wcvp_species(
        "Sarothamnus scoparius", genus="Sarothamnus", family="Fabaceae",
        authorship="(L.) Wimm.", accepted=accepted,
    )
    syn = get_or_create_from_wcvp_data(session, synonym_fields)
    acc = session.get(Taxon, syn.accepted_name_usage_id)

    assert syn.id != acc.id
    assert syn.scientific_name == "Sarothamnus scoparius"
    assert session.get(Taxon, syn.parent_name_usage_id).scientific_name == "Sarothamnus"
    assert session.get(Taxon, acc.parent_name_usage_id).scientific_name == "Cytisus"
