"""Taxonomy checklist tree: synonym-aware specimen count rollup (#151)."""
from app.models import CollectionObject, Taxon, TaxonDetermination
from app.models.base import _utcnow
from app.services.taxa import synonymize
import app.services.taxonomy as tax_svc
from tests.helpers import ensure_repo


def _taxon(session, name, rank, parent=None, code="ICZN"):
    t = Taxon(scientific_name=name, taxon_rank=rank, nomenclatural_code=code,
              parent_name_usage_id=(parent.id if parent else None),
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


def _specimen(session, taxon, catalog):
    co = CollectionObject(catalog_number=catalog, repository_id=ensure_repo(session, "Doe"),
                          created_at=_utcnow(), updated_at=_utcnow())
    session.add(co); session.flush()
    session.add(TaxonDetermination(collection_object_id=co.id, taxon_id=taxon.id,
                                   is_current=1, created_at=_utcnow(), updated_at=_utcnow()))
    session.flush()
    return co


def _node(nodes, taxon_id):
    """Find the `taxon-<id>` node anywhere in the tree (DFS)."""
    for n in nodes:
        if n.get("id") == f"taxon-{taxon_id}":
            return n
        found = _node(n.get("children", []), taxon_id)
        if found:
            return found
    return None


def _syn_node(nodes, taxon_id):
    for n in nodes:
        if n.get("id") == f"syn-{taxon_id}":
            return n
        found = _syn_node(n.get("children", []), taxon_id)
        if found:
            return found
    return None


def test_spec_count_rolls_up_synonym_determined_specimens(session):
    """#151: a specimen determined under a synonym must count toward the accepted
    name's spec_count (the tree's displayed total must agree with what Explore's
    taxon filter actually retrieves for that accepted name) — while the synonym's
    own row still shows its own count, so the name-as-recorded stays visible."""
    fam = _taxon(session, "Curculionidae", "family")
    gen = _taxon(session, "Entimus", "genus", parent=fam)
    accepted = _taxon(session, "Entimus sastrei", "species", parent=gen)
    syn = _taxon(session, "Entimus formosus", "species", parent=gen)
    synonymize(session, name_id=syn.id, accepted_id=accepted.id)
    _specimen(session, accepted, "A1")
    _specimen(session, syn, "A2")

    tree = tax_svc.build_taxonomy_tree(session, nomenclatural_code="ICZN")

    acc_node = _node(tree, accepted.id)
    assert acc_node["spec_count"] == 2          # A1 (own) + A2 (synonym) rolled up

    syn_node = _syn_node(tree, syn.id)
    assert syn_node["spec_count"] == 1          # the synonym's own row still shows A2

    genus_node = _node(tree, gen.id)
    assert genus_node["spec_count"] == 2        # not double-counted on the way up
