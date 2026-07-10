"""find_taxon_by_name: exact match only, no authorship guessing (#2).

The old two-token fallback could not tell a trailing author from a trinomial epithet, so it
silently downgraded a subspecies to its species. dwc:scientificName is the composed name
WITHOUT authorship (CLAUDE.md §4), so an exact match is the whole contract; a dirty,
authorship-laden name is refused (returns None) and flagged, never mis-resolved.
"""
import pytest

from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import (
    compose_scientific_name, find_taxon_by_name, scientific_name_has_authorship,
)


def _sp(session, *, element, rank, parent=None, code="ICZN"):
    t = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
              parent_name_usage_id=parent.id if parent else None,
              nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    t.scientific_name = compose_scientific_name(session, t)
    session.flush()
    return t


def test_exact_binomial_resolves(session):
    g = _sp(session, element="Carabus", rank="genus")
    sp = _sp(session, element="baudii", rank="species", parent=g)
    assert find_taxon_by_name(session, "Carabus baudii") is sp


def test_trinomial_never_downgrades_to_its_species(session):
    """The bug: `Carabus baudii fenestrellanus` must NOT resolve to `Carabus baudii`."""
    g = _sp(session, element="Carabus", rank="genus")
    _sp(session, element="baudii", rank="species", parent=g)   # the species exists...
    # ...the subspecies does not. The old code returned the species; now it returns None.
    assert find_taxon_by_name(session, "Carabus baudii fenestrellanus") is None


def test_trinomial_resolves_when_it_actually_exists(session):
    g = _sp(session, element="Carabus", rank="genus")
    sp = _sp(session, element="baudii", rank="species", parent=g)
    ssp = _sp(session, element="fenestrellanus", rank="subspecies", parent=sp)
    assert find_taxon_by_name(session, "Carabus baudii fenestrellanus") is ssp


def test_authorship_laden_name_does_not_resolve(session):
    """With authorship left in the name column there is no exact match — refuse, don't guess."""
    g = _sp(session, element="Sitona", rank="genus")
    _sp(session, element="lineatus", rank="species", parent=g)
    assert find_taxon_by_name(session, "Sitona lineatus Linnaeus") is None


def test_empty_and_ambiguous(session):
    assert find_taxon_by_name(session, "") is None
    assert find_taxon_by_name(session, "   ") is None


# ── the authorship detector ─────────────────────────────────────────────────────
@pytest.mark.parametrize("name, flagged", [
    ("Sitona lineatus", False),                       # clean binomial
    ("Carabus baudii fenestrellanus", False),         # clean trinomial (all epithets)
    ("Otiorhynchus (Otiorhynchus) armadillo", False), # clean, with subgenus
    ("Sitona lineatus Linnaeus", True),               # trailing author
    ("Sitona lineatus (Linnaeus, 1758)", True),       # author + year, parenthesised
    ("Carabus violaceus de Geer", True),              # lowercase particle + capitalised author
    ("Otiorhynchus (Otiorhynchus) armadillo Rossi", True),
    ("Sitona", False),                                # bare genus
    ("", False),
])
def test_scientific_name_has_authorship(name, flagged):
    assert scientific_name_has_authorship(name) is flagged
