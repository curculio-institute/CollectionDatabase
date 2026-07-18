"""merge_taxa — de-duplication of two rows that are the SAME name (typo / subgenus dup).

The Carabus (Eucarabus) arvensis vs Carabus arvensis case: a subgenus-blind import created a
second row and specimens attached to it. Merge re-points every reference onto the surviving
row and deletes the other; it is NOT synonymisation (which stays in synonymize()).
"""
import pytest

from app.models import (
    Taxon, TaxonDetermination, CollectionObject, BiologicalAssociation,
    BiologicalRelationship,
)
from app.models.base import _utcnow
from app.services.taxa import (
    compose_scientific_name, compose_full_name, merge_taxa, merge_taxa_preview, synonymize,
)
from app.services.specimens import create_collection_object, create_determination
from tests.helpers import ensure_repo


def _sp(session, *, element, rank, parent=None, code="ICZN", authorship=None):
    t = Taxon(name_element=element, scientific_name=element, taxon_rank=rank,
              parent_name_usage_id=parent.id if parent else None,
              scientific_name_authorship=authorship,
              nomenclatural_code=code, created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    t.scientific_name = compose_scientific_name(session, t)
    session.flush()
    return t


def _co_with_det(session, taxon, cat):
    co = create_collection_object(session, collecting_event_id=None,
                                  catalog_number=cat, repository_id=ensure_repo(session, "Doe"))
    create_determination(session, collection_object_id=co.id, taxon_id=taxon.id,
                         verbatim_identification=compose_full_name(session, taxon))
    return co


def test_merge_repoints_determinations_and_deletes_absorb(session):
    g = _sp(session, element="Carabus", rank="genus")
    subg = _sp(session, element="Eucarabus", rank="subgenus", parent=g)
    keep = _sp(session, element="arvensis", rank="species", parent=subg,
               authorship="Herbst, 1784")   # Carabus (Eucarabus) arvensis
    absorb = _sp(session, element="arvensis", rank="species", parent=g,
                 authorship="Herbst, 1784")  # Carabus arvensis (the dup)

    _co_with_det(session, keep, "aa01")
    _co_with_det(session, absorb, "aa02")
    _co_with_det(session, absorb, "aa03")

    prev = merge_taxa_preview(session, keep.id, absorb.id)
    assert prev.determinations == 2 and prev.blocker is None
    assert prev.keep_label == "Carabus (Eucarabus) arvensis Herbst, 1784"

    merge_taxa(session, keep.id, absorb.id)
    session.expire_all()

    assert session.get(Taxon, absorb.id) is None                     # absorb gone
    dets = session.query(TaxonDetermination).all()
    assert {d.taxon_id for d in dets} == {keep.id}                    # all point at keep
    # the frozen name-as-used is NOT rewritten — history stands
    assert sorted(d.verbatim_identification for d in dets) == [
        "Carabus (Eucarabus) arvensis Herbst, 1784",
        "Carabus arvensis Herbst, 1784",
        "Carabus arvensis Herbst, 1784",
    ]


def test_merge_rehomes_children_and_synonyms(session):
    g = _sp(session, element="Carabus", rank="genus")
    keep = _sp(session, element="Carabus", rank="genus")   # a duplicate GENUS to keep
    # absorb (g) has a child species and a synonym pointing at it
    child = _sp(session, element="violaceus", rank="species", parent=g)
    syn = _sp(session, element="Carabus", rank="genus")
    synonymize(session, name_id=syn.id, accepted_id=g.id)

    merge_taxa(session, keep.id, g.id)
    session.expire_all()

    assert session.get(Taxon, g.id) is None
    assert session.get(Taxon, child.id).parent_name_usage_id == keep.id
    assert session.get(Taxon, child.id).scientific_name == "Carabus violaceus"   # recomposed
    assert session.get(Taxon, syn.id).accepted_name_usage_id == keep.id


def test_merge_moves_biological_associations(session):
    g = _sp(session, element="Quercus", rank="genus", code="ICN")
    keep = _sp(session, element="robur", rank="species", parent=g)
    absorb = _sp(session, element="robur", rank="species", parent=g)
    beetle_g = _sp(session, element="Curculio", rank="genus")
    beetle = _sp(session, element="glandium", rank="species", parent=beetle_g)
    co = create_collection_object(session, collecting_event_id=None,
                                  catalog_number="bb01", repository_id=ensure_repo(session, "Doe"))
    rel = BiologicalRelationship(name="collected_from", created_at=_utcnow(), updated_at=_utcnow())
    session.add(rel); session.flush()
    ba = BiologicalAssociation(
        subject_collection_object_id=co.id, object_taxon_id=absorb.id,
        biological_relationship_id=rel.id, created_at=_utcnow(), updated_at=_utcnow())
    session.add(ba); session.flush()

    merge_taxa(session, keep.id, absorb.id)
    session.expire_all()
    assert session.get(BiologicalAssociation, ba.id).object_taxon_id == keep.id


def test_merge_refuses_synonym(session):
    g = _sp(session, element="Otiorhynchus", rank="genus")
    acc = _sp(session, element="fortis", rank="species", parent=g)
    syn = _sp(session, element="forticollis", rank="species", parent=g)
    synonymize(session, name_id=syn.id, accepted_id=acc.id)
    prev = merge_taxa_preview(session, acc.id, syn.id)
    assert prev.blocker and "synonym" in prev.blocker.lower()
    with pytest.raises(ValueError, match="synonym"):
        merge_taxa(session, acc.id, syn.id)


def test_merge_refuses_different_ranks(session):
    g = _sp(session, element="Carabus", rank="genus")
    sp = _sp(session, element="arvensis", rank="species", parent=g)
    with pytest.raises(ValueError, match="[Dd]ifferent rank"):
        merge_taxa(session, g.id, sp.id)


def test_merge_refuses_self(session):
    g = _sp(session, element="Carabus", rank="genus")
    with pytest.raises(ValueError):
        merge_taxa(session, g.id, g.id)
