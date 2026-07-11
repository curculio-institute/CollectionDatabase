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
        nomenclatural_code="ICZN",
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


# ── Code-specific ranks + genus-group form (ICZN brackets vs ICN connectors) ──────────
# A rank belongs to a nomenclatural code, and the two codes write the genus group
# differently. Zoology brackets the subgenus and carries it into the binomial; botany
# spells the connector out and does NOT — a botanical binomial is genus + epithet, so a
# species under a subgenus OR a section still composes as a plain two-word name.

def test_compose_icn_subgenus_uses_connector_not_brackets(session):
    """Taraxacum subg. Palustria — not 'Taraxacum (Palustria)', which is the ICZN form."""
    genus = _add(session, element="Taraxacum", rank="genus", code="ICN")
    subg = _add(session, element="Palustria", rank="subgenus", parent=genus, code="ICN")
    assert compose_scientific_name(session, subg) == "Taraxacum subg. Palustria"


def test_compose_icn_section(session):
    """Sections are ICN genus-group ranks: Taraxacum sect. Ruderalia."""
    genus = _add(session, element="Taraxacum", rank="genus", code="ICN")
    sect = _add(session, element="Ruderalia", rank="section", parent=genus, code="ICN")
    subsect = _add(session, element="Vulgaria", rank="subsection", parent=genus, code="ICN")
    assert compose_scientific_name(session, sect) == "Taraxacum sect. Ruderalia"
    assert compose_scientific_name(session, subsect) == "Taraxacum subsect. Vulgaria"


def test_compose_icn_species_under_section_is_a_plain_binomial(session):
    """The section is classificatory, not part of the name: Taraxacum officinale."""
    genus = _add(session, element="Taraxacum", rank="genus", code="ICN")
    sect = _add(session, element="Ruderalia", rank="section", parent=genus, code="ICN")
    sp = _add(session, element="officinale", rank="species", parent=sect, code="ICN")
    var = _add(session, element="alpinum", rank="variety", parent=sp, code="ICN")
    assert compose_scientific_name(session, sp) == "Taraxacum officinale"
    assert compose_scientific_name(session, var) == "Taraxacum officinale var. alpinum"


def test_compose_icn_species_under_subgenus_is_a_plain_binomial(session):
    """Likewise for a botanical subgenus — no '(Palustria)' inside the binomial."""
    genus = _add(session, element="Taraxacum", rank="genus", code="ICN")
    subg = _add(session, element="Palustria", rank="subgenus", parent=genus, code="ICN")
    sp = _add(session, element="palustre", rank="species", parent=subg, code="ICN")
    assert compose_scientific_name(session, sp) == "Taraxacum palustre"


def test_compose_iczn_genus_group_still_brackets(session):
    """Regression guard: the zoological form must be untouched by the ICN work."""
    genus = _add(session, element="Otiorhynchus", rank="genus")
    subg = _add(session, element="Nihus", rank="subgenus", parent=genus)
    sp = _add(session, element="armadillo", rank="species", parent=subg)
    assert compose_scientific_name(session, subg) == "Otiorhynchus (Nihus)"
    assert compose_scientific_name(session, sp) == "Otiorhynchus (Nihus) armadillo"


def test_ranks_are_code_specific(session):
    """A beetle is never offered 'variety'; a plant is never offered 'superfamily'."""
    from app.services.taxa import RANKS_BY_CODE, TAXON_RANKS, ranks_for

    iczn, icn = ranks_for("ICZN"), ranks_for("ICN")
    for botanical_only in ("variety", "subvariety", "form", "subform", "section", "subsection"):
        assert botanical_only not in iczn
        assert botanical_only in icn
    for zoological_only in ("superfamily", "supertribe", "superorder"):
        assert zoological_only in iczn
        assert zoological_only not in icn
    # Shared backbone still present in both.
    for shared in ("genus", "subgenus", "species", "subspecies", "family"):
        assert shared in iczn and shared in icn
    # Every per-code list stays a SUBSEQUENCE of the master ordering, so the index-based
    # hierarchy comparisons (parent rank must be above child rank) remain valid.
    for code, rs in RANKS_BY_CODE.items():
        idx = [TAXON_RANKS.index(r) for r in rs]
        assert idx == sorted(idx), f"{code} ranks are not in TAXON_RANKS order"
    # An unknown code offers everything rather than nothing (the editor narrows it later).
    assert set(ranks_for(None)) == set(TAXON_RANKS)
