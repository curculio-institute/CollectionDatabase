"""Manual taxon ordering for the checklist (family-and-above), display-only (#40)."""
from app.models import Taxon
from app.models.base import _utcnow
import app.services.taxonomy as tax


def _t(session, name, rank, parent=None):
    t = Taxon(scientific_name=name, taxon_rank=rank,
              parent_name_usage_id=(parent.id if parent else None),
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


def _fams(session):
    sf = _t(session, "Curculionoidea", "superfamily")
    a = _t(session, "Anthribidae", "family", sf)
    b = _t(session, "Brentidae", "family", sf)
    c = _t(session, "Curculionidae", "family", sf)
    return sf, a, b, c


def test_default_order_is_alphabetical(session):
    sf, a, b, c = _fams(session)
    sibs = sorted([a, b, c], key=tax.order_key)
    assert [s.scientific_name for s in sibs] == ["Anthribidae", "Brentidae", "Curculionidae"]


def test_move_family_reorders_and_materialises(session):
    sf, a, b, c = _fams(session)
    tax.move_taxon(session, c.id, -1)        # Curculionidae up → swaps with Brentidae
    session.flush()
    order = [t.scientific_name for t in sorted([a, b, c], key=tax.order_key)]
    assert order == ["Anthribidae", "Curculionidae", "Brentidae"]
    # all siblings now carry an explicit sort_order (materialised)
    assert all(t.sort_order is not None for t in (a, b, c))


def test_move_is_stable_at_ends(session):
    sf, a, b, c = _fams(session)
    tax.move_taxon(session, a.id, -1)        # already first → no-op
    session.flush()
    order = [t.scientific_name for t in sorted([a, b, c], key=tax.order_key)]
    assert order == ["Anthribidae", "Brentidae", "Curculionidae"]


def test_below_family_is_not_orderable(session):
    sf, a, b, c = _fams(session)
    g1 = _t(session, "Apion", "genus", c)
    g2 = _t(session, "Baris", "genus", c)
    tax.move_taxon(session, g2.id, -1)       # genus → ignored
    session.flush()
    assert g1.sort_order is None and g2.sort_order is None


def test_tree_respects_manual_order(session):
    sf, a, b, c = _fams(session)
    tax.move_taxon(session, c.id, -1)
    session.flush()
    tree = tax.build_taxonomy_tree(session)
    # find the superfamily node, read its family children order
    def _find(nodes, name):
        for n in nodes:
            if n["name"] == name:
                return n
            hit = _find(n.get("children", []), name)
            if hit:
                return hit
        return None
    sf_node = _find(tree, "Curculionoidea")
    fams = [c2["name"] for c2 in sf_node["children"]]
    assert fams == ["Anthribidae", "Curculionidae", "Brentidae"]
