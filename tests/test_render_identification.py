"""Phase 5 (#35) — determination rendering + frozen verbatim identification.

A determination freezes dwc:verbatimIdentification (the composed name at save
time, qualifier-free); the open-nomenclature qualifier lives separately and is
rendered right after the genus-group by render_identification(). Re-classifying
the taxon later must NOT change a saved determination's rendered name, but the
specimen stays findable via the live taxon_id.
"""
import pytest

from app.models import Taxon
from app.models.base import _utcnow
from app.services.taxa import (
    split_genus_group,
    render_identification,
    compose_scientific_name,
    reparent,
)
from app.services.specimens import create_collection_object, create_determination


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
def test_render_identification(name, qual, expected):
    assert render_identification(name, qual) == expected


# ---------------------------------------------------------------------------
# Frozen verbatim — re-classification does not rewrite a saved determination
# ---------------------------------------------------------------------------

def _sp(session, *, element, rank, parent=None, code="ICZN"):
    t = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
              parent_name_usage_id=parent.id if parent else None,
              nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    t.scientific_name = compose_scientific_name(session, t)
    session.flush()
    return t


def test_determination_freezes_name_against_reclassification(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    other = _sp(session, element="Xanthorhynchus", rank="genus")
    sp = _sp(session, element="forticollis", rank="species", parent=genus)
    assert sp.scientific_name == "Otiorhynchus forticollis"

    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa01", collection_code="Jilg", institution_code="Jilg",
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=sp.id,
        identification_qualifier="cf.",
        verbatim_identification=compose_scientific_name(session, sp),
    )
    assert det.verbatim_identification == "Otiorhynchus forticollis"
    assert render_identification(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus cf. forticollis"

    # Re-home the species to another genus: the live taxon name changes …
    reparent(session, taxon_id=sp.id, new_parent_id=other.id)
    session.refresh(sp); session.refresh(det)
    assert sp.scientific_name == "Xanthorhynchus forticollis"        # live concept moved
    # … but the saved determination's frozen verbatim is untouched …
    assert det.verbatim_identification == "Otiorhynchus forticollis"
    assert render_identification(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus cf. forticollis"
    # … and the specimen is still findable via the live concept (taxon_id).
    assert det.taxon_id == sp.id


def test_determination_genus_only_renders_sp(session):
    genus = _sp(session, element="Otiorhynchus", rank="genus")
    co = create_collection_object(
        session, collecting_event_id=None,
        catalog_number="aa02", collection_code="Jilg", institution_code="Jilg",
    )
    det = create_determination(
        session, collection_object_id=co.id, taxon_id=genus.id,
        identification_qualifier="sp.",
        verbatim_identification=compose_scientific_name(session, genus),
    )
    assert render_identification(det.verbatim_identification, det.identification_qualifier) \
        == "Otiorhynchus sp."
