"""Phase 5 (#35) — determination rendering + frozen verbatim identification.

A determination freezes dwc:verbatimIdentification (the composed FULL name at save
time — bare name plus authorship, qualifier-free); the open-nomenclature qualifier
lives separately and is placed right after the genus-group by _place_qualifier() (the
private building block of render_full_name). Re-classifying the taxon later must NOT
change a saved determination's rendered name, but the specimen stays findable via the
live taxon_id.
"""
import pytest

from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import (
    split_genus_group,
    _place_qualifier,
    render_full_name,
    render_full_name_frozen,
    compose_scientific_name,
    compose_full_name,
    reparent,
)
from app.services.specimens import create_collection_object, create_determination
from tests.helpers import ensure_repo


# ---------------------------------------------------------------------------
# Pure rendering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Otiorhynchus forticollis", ("Otiorhynchus", "forticollis")),
    ("Otiorhynchus (Nihus) forticollis", ("Otiorhynchus (Nihus)", "forticollis")),
    ("Otiorhynchus", ("Otiorhynchus", "")),
    ("Otiorhynchus (Nihus)", ("Otiorhynchus (Nihus)", "")),
    ("", ("", "")),
])
def test_split_genus_group(name, expected):
    assert split_genus_group(name) == expected


@pytest.mark.parametrize("name, qual, expected", [
    ("Otiorhynchus forticollis", "cf.", "Otiorhynchus cf. forticollis"),
    ("Otiorhynchus (Nihus) forticollis", "aff.", "Otiorhynchus (Nihus) aff. forticollis"),
    ("Otiorhynchus", "sp.", "Otiorhynchus sp."),               # genus row → empty rest
    ("Otiorhynchus (Nihus)", "sp.", "Otiorhynchus (Nihus) sp."),
    ("Otiorhynchus forticollis", None, "Otiorhynchus forticollis"),
    ("Otiorhynchus forticollis", "", "Otiorhynchus forticollis"),
    ("Otiorhynchus", "cf.", "Otiorhynchus cf."),               # rare: rest empty
    ("Achillea millefolium alpina", "cf.", "Achillea cf. millefolium alpina"),
])
def test_place_qualifier(name, qual, expected):
    assert _place_qualifier(name, qual) == expected


# ---------------------------------------------------------------------------
# render_full_name — the single renderer: italic name, roman author, roman qualifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, qual, auth, rank, expected", [
    # species: genus group italic, author roman
    ("Otiorhynchus armadillo", None, "(Rossi, 1792)", "species",
     "<i>Otiorhynchus armadillo</i> (Rossi, 1792)"),
    # qualifier after the genus group, roman
    ("Otiorhynchus forticollis", "cf.", "(Stierlin, 1861)", "species",
     "<i>Otiorhynchus</i> cf. <i>forticollis</i> (Stierlin, 1861)"),
    # genus-only "sp." determination
    ("Otiorhynchus", "sp.", "Germar, 1822", "genus",
     "<i>Otiorhynchus</i> sp. Germar, 1822"),
    # a family is NOT italic, author still roman
    ("Curculionidae", None, "Latreille, 1802", "family",
     "Curculionidae Latreille, 1802"),
])
def test_render_full_name(name, qual, auth, rank, expected):
    assert render_full_name(name, qualifier=qual, authorship=auth, taxon_rank=rank) == expected


def test_render_full_name_frozen_splits_authorship_out():
    """A frozen verbatim carries authorship inside the string; the renderer splits it
    back so the author is roman and only the name is italic."""
    frozen = "Otiorhynchus armadillo (Rossi, 1792)"
    assert render_full_name_frozen(frozen, qualifier="cf.", taxon_rank="species") \
        == "<i>Otiorhynchus</i> cf. <i>armadillo</i> (Rossi, 1792)"


# ---------------------------------------------------------------------------
# Frozen verbatim — re-classification does not rewrite a saved determination
# ---------------------------------------------------------------------------

def _sp(session, *, element, rank, parent=None, code="ICZN", authorship=None):
    t = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
              parent_name_usage_id=parent.id if parent else None,
              scientific_name_authorship=authorship,
              nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    t.scientific_name = compose_scientific_name(session, t)
    session.flush()
    return t


# ---------------------------------------------------------------------------
# compose_full_name — the frozen name carries authorship (authorship IS part of a name)
# ---------------------------------------------------------------------------

def test_compose_full_name_appends_authorship(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    sp = _sp(session, element="armadillo", rank="species", parent=genus,
             authorship="(Rossi, 1792)")
    assert compose_scientific_name(session, sp) == "Otiorhynchus armadillo"
    assert compose_full_name(session, sp) == "Otiorhynchus armadillo (Rossi, 1792)"


def test_compose_full_name_without_authorship_is_bare(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    sp = _sp(session, element="armadillo", rank="species", parent=genus)
    assert compose_full_name(session, sp) == "Otiorhynchus armadillo"


def test_determination_freezes_full_name_with_authorship(session):
    """The interactive freeze stores name + authorship; a later split renders it correctly."""
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    sp = _sp(session, element="armadillo", rank="species", parent=genus,
             authorship="(Rossi, 1792)")
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa10", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=sp.id,
        verbatim_identification=compose_full_name(session, sp),
    )
    assert det.verbatim_identification == "Otiorhynchus armadillo (Rossi, 1792)"
    assert render_full_name_frozen(det.verbatim_identification,
                                   qualifier=det.identification_qualifier,
                                   taxon_rank="species") \
        == "<i>Otiorhynchus armadillo</i> (Rossi, 1792)"


def test_determination_freezes_name_against_reclassification(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    other = _sp(session, element="Xanthorhynchus", rank="genus")
    sp = _sp(session, element="forticollis", rank="species", parent=genus)
    assert sp.scientific_name == "Otiorhynchus forticollis"

    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa01", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=sp.id,
        identification_qualifier="cf.",
        verbatim_identification=compose_scientific_name(session, sp),
    )
    assert det.verbatim_identification == "Otiorhynchus forticollis"
    assert _place_qualifier(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus cf. forticollis"

    # Re-home the species to another genus: the live taxon name changes …
    reparent(session, taxon_id=sp.id, new_parent_id=other.id)
    session.refresh(sp); session.refresh(det)
    assert sp.scientific_name == "Xanthorhynchus forticollis"        # live concept moved
    # … but the saved determination's frozen verbatim is untouched …
    assert det.verbatim_identification == "Otiorhynchus forticollis"
    assert _place_qualifier(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus cf. forticollis"
    # … and the specimen is still findable via the live concept (taxon_id).
    assert det.taxon_id == sp.id


def test_determination_genus_only_renders_sp(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa02", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=genus.id,
        identification_qualifier="sp.",
        verbatim_identification=compose_scientific_name(session, genus),
    )
    assert _place_qualifier(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus sp."


# ---------------------------------------------------------------------------
# Correcting a mis-picked taxon (#54): re-point + RE-freeze
# ---------------------------------------------------------------------------
# Distinct from re-classification above: there the taxon moved and the saved determination
# must NOT change. Here the user says "this determination names the wrong taxon", so the
# frozen name must follow — otherwise the row claims a name its own taxon_id contradicts.

def test_correcting_the_taxon_refreezes_the_verbatim_name(session):
    from app.services.specimens import update_determination_taxon

    genus = _sp(session, element="Otiorhynchus", rank="genus")
    wrong = _sp(session, element="fortis", rank="species", parent=genus)
    right = _sp(session, element="crypticus", rank="species", parent=genus)

    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa02", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=wrong.id,
        identified_by_id=None, date_identified="2024-06-01",
        identification_qualifier="cf.",
        verbatim_identification=compose_scientific_name(session, wrong),
    )
    assert det.verbatim_identification == "Otiorhynchus fortis"

    update_determination_taxon(session, det.id, taxon_id=right.id)
    session.refresh(det)

    assert det.taxon_id == right.id
    assert det.verbatim_identification == "Otiorhynchus crypticus"   # re-frozen
    # the assertion's provenance is untouched — who identified it, and when, is unchanged
    assert det.date_identified == "2024-06-01"
    assert det.identification_qualifier == "cf."
    assert _place_qualifier(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus cf. crypticus"


def test_correcting_the_taxon_never_leaves_the_row_contradicting_itself(session):
    """verbatim must always match the composed name of the row's own taxon_id."""
    from app.services.specimens import update_determination_taxon

    g1 = _sp(session, element="Otiorhynchus", rank="genus")
    g2 = _sp(session, element="Curculio", rank="genus")
    a = _sp(session, element="fortis", rank="species", parent=g1)
    b = _sp(session, element="glandium", rank="species", parent=g2)

    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa03", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=a.id,
        verbatim_identification=compose_scientific_name(session, a),
    )
    update_determination_taxon(session, det.id, taxon_id=b.id)
    session.refresh(det)
    live = compose_scientific_name(session, session.get(Taxon, det.taxon_id))
    assert det.verbatim_identification == live == "Curculio glandium"


def test_correcting_to_a_missing_taxon_is_refused(session):
    from app.services.specimens import update_determination_taxon
    g = _sp(session, element="Otiorhynchus", rank="genus")
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa04", repository_id=ensure_repo(session, "Doe"),
    )
    det = create_determination(session, collection_object_id=co.id, taxon_id=g.id,
                              verbatim_identification="Otiorhynchus")
    with pytest.raises(ValueError, match="Taxon .* not found"):
        update_determination_taxon(session, det.id, taxon_id=999999)
