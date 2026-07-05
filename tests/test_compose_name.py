"""Phase 2 (#32) — compose_scientific_name + recompose_subtree.

Composition is uniform for valid names and synonyms (a synonym's parent is its
own lineage). Synonym *link* cases (acceptedNameUsageID) are covered in Phase 4,
once the strict parent-match trigger is retired; here we exercise the pure
composition logic on accepted rows, including a row under a different genus to
prove the parent chain (not any stored combination) drives the result.
"""
import pytest

from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import compose_scientific_name, recompose_subtree


def _add(session, *, element, rank, parent=None, code="ICZN"):
    t = Taxon(
        name_element=element,
        scientific_name=element,  # placeholder; recompose overwrites
        taxon_rank=rank,
        parent_name_usage_id=parent.id if parent else None,
        nomenclatural_code=code,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(t)
    session.flush()
    return t


@pytest.fixture
def tree(session):
    """A small Otiorhynchus tree + a Curculio genus for the cross-genus case."""
    genus = _add(session, element="Otiorhynchus", rank="genus")
    subg = _add(session, element="Nihus", rank="subgenus", parent=genus)
    nominate = _add(session, element="Otiorhynchus", rank="subgenus", parent=genus)
    sp_plain = _add(session, element="crypticus", rank="species", parent=genus)
    sp_sub = _add(session, element="carinatopunctatus", rank="species", parent=subg)
    ssp = _add(session, element="alpinus", rank="subspecies", parent=sp_plain)
    curculio = _add(session, element="Curculio", rank="genus")
    cross = _add(session, element="forticollis", rank="species", parent=curculio)
    return dict(genus=genus, subg=subg, nominate=nominate, sp_plain=sp_plain,
                sp_sub=sp_sub, ssp=ssp, curculio=curculio, cross=cross)


def test_genus(session, tree):
    assert compose_scientific_name(session, tree["genus"]) == "Otiorhynchus"


def test_subgenus_disambiguated(session, tree):
    # nominotypical subgenus must not collapse to the bare genus name
    assert compose_scientific_name(session, tree["nominate"]) == "Otiorhynchus (Otiorhynchus)"
    assert compose_scientific_name(session, tree["subg"]) == "Otiorhynchus (Nihus)"


def test_species_plain(session, tree):
    assert compose_scientific_name(session, tree["sp_plain"]) == "Otiorhynchus crypticus"


def test_species_with_subgenus(session, tree):
    assert compose_scientific_name(session, tree["sp_sub"]) == "Otiorhynchus (Nihus) carinatopunctatus"


def test_subspecies_iczn_no_connector(session, tree):
    assert compose_scientific_name(session, tree["ssp"]) == "Otiorhynchus crypticus alpinus"


def test_infraspecific_icn_connectors(session):
    g = _add(session, element="Rosa", rank="genus", code="ICN")
    sp = _add(session, element="canina", rank="species", parent=g, code="ICN")
    var = _add(session, element="dumalis", rank="variety", parent=sp, code="ICN")
    ssp = _add(session, element="montana", rank="subspecies", parent=sp, code="ICN")
    assert compose_scientific_name(session, var) == "Rosa canina var. dumalis"
    assert compose_scientific_name(session, ssp) == "Rosa canina subsp. montana"


def test_composition_uses_parent_chain_not_stored_genus(session, tree):
    # The "Curculio forticollis" row lives under Curculio: composition follows the
    # parent, regardless of any accepted concept it might later point to.
    assert compose_scientific_name(session, tree["cross"]) == "Curculio forticollis"


def test_fallback_when_name_element_missing(session):
    t = Taxon(
        name_element=None,
        scientific_name="Legacy name",
        taxon_rank="species",
        created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(t); session.flush()
    assert compose_scientific_name(session, t) == "Legacy name"


def test_recompose_subtree_cascades_on_genus_rename(session, tree):
    # Rename the genus element; descendants must recompose.
    tree["genus"].name_element = "Xanthorhynchus"
    recompose_subtree(session, tree["genus"])
    assert tree["genus"].scientific_name == "Xanthorhynchus"
    assert tree["sp_plain"].scientific_name == "Xanthorhynchus crypticus"
    assert tree["sp_sub"].scientific_name == "Xanthorhynchus (Nihus) carinatopunctatus"
    assert tree["ssp"].scientific_name == "Xanthorhynchus crypticus alpinus"
    # the Curculio side is untouched
    assert tree["cross"].scientific_name in ("forticollis", "Curculio forticollis")


def test_reparent_recomposes_subtree(session, tree):
    # Moving a species to another genus (via the reparent service op) must
    # recompose its name and its descendants'.
    from app.services.taxa import reparent
    # seed composed names so the assertion below is meaningful
    recompose_subtree(session, tree["genus"])
    assert tree["sp_plain"].scientific_name == "Otiorhynchus crypticus"
    assert tree["ssp"].scientific_name == "Otiorhynchus crypticus alpinus"
    reparent(session, taxon_id=tree["sp_plain"].id, new_parent_id=tree["curculio"].id)
    session.refresh(tree["sp_plain"]); session.refresh(tree["ssp"])
    assert tree["sp_plain"].scientific_name == "Curculio crypticus"
    assert tree["ssp"].scientific_name == "Curculio crypticus alpinus"


def test_compose_icn_subvariety_and_subform(session):
    """#71: POWO ICN sub-ranks (subvariety/subform) compose as proper trinomials
    with the right connector — previously they fell through to the bare epithet."""
    genus = _add(session, element="Achillea", rank="genus", code="ICN")
    sp = _add(session, element="millefolium", rank="species", parent=genus, code="ICN")
    subvar = _add(session, element="alpina", rank="subvariety", parent=sp, code="ICN")
    subform = _add(session, element="minor", rank="subform", parent=sp, code="ICN")
    assert compose_scientific_name(session, subvar) == "Achillea millefolium subvar. alpina"
    assert compose_scientific_name(session, subform) == "Achillea millefolium subf. minor"
