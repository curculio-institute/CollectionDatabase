"""Explore favorites — saved searches service (#137)."""
import pytest

from app.models import Taxon, Person
from app.models.base import _utcnow
import app.services.saved_searches as ss
from tests.helpers import ensure_repo


def _taxon(session, name, rank="family"):
    t = Taxon(scientific_name=name, taxon_rank=rank, nomenclatural_code="ICZN",
              created_at=_utcnow(), updated_at=_utcnow())
    session.add(t); session.flush()
    return t


def _groups(taxon_id):
    return [{"op": "or", "facets": [
        {"kind": "taxon", "key": taxon_id, "label": "Carabidae", "tag": "Family"}]}]


def test_create_list_and_unique_name(session):
    t = _taxon(session, "Carabidae")
    fav = ss.create(session, "Beetles", _groups(t.id))
    assert fav.id and [f.name for f in ss.list_searches(session)] == ["Beetles"]
    with pytest.raises(ValueError):
        ss.create(session, "Beetles", _groups(t.id))            # duplicate name
    with pytest.raises(ValueError):
        ss.create(session, "  ", _groups(t.id))                 # blank name
    with pytest.raises(ValueError):
        ss.create(session, "Empty", [{"op": "and", "facets": []}])  # nothing to save


def test_single_default(session):
    t = _taxon(session, "Carabidae")
    a = ss.create(session, "A", _groups(t.id))
    b = ss.create(session, "B", _groups(t.id))
    ss.set_default(session, a.id)
    assert ss.get_default(session).id == a.id
    ss.set_default(session, b.id)                                # switches, never two
    assert ss.get_default(session).id == b.id
    ss.set_default(session, None)                               # clear
    assert ss.get_default(session) is None


def test_resolve_refreshes_label_and_flags_stale(session):
    t = _taxon(session, "Carabidae")
    fav = ss.create(session, "Beetles", _groups(t.id))
    # rename the taxon → resolve must show the CURRENT name, not the saved one
    t.scientific_name = "Carabidae (renamed)"
    session.flush()
    r = ss.resolve(session, fav)
    assert r["stale"] == 0
    assert r["groups"][0]["facets"][0]["label"] == "Carabidae (renamed)"
    # delete the taxon → the facet is stale, not silently applied
    session.delete(t); session.flush()
    r2 = ss.resolve(session, fav)
    assert r2["stale"] == 1 and r2["groups"][0]["facets"][0]["stale"] is True


def test_apply_groups_drops_stale_and_empty_groups(session):
    t1 = _taxon(session, "Carabidae")
    t2 = _taxon(session, "Curculionidae")
    fav = ss.create(session, "Two", [
        {"op": "or", "facets": [
            {"kind": "taxon", "key": t1.id, "label": "Carabidae", "tag": "Family"},
            {"kind": "taxon", "key": t2.id, "label": "Curculionidae", "tag": "Family"}]},
        {"op": "and", "facets": [
            {"kind": "collector", "key": "Ghost", "label": "Ghost", "tag": "Collector"}]},
    ])
    session.delete(t2); session.flush()                         # one facet + one whole group go stale
    resolved = ss.resolve(session, fav)["groups"]
    applied = ss.apply_groups(resolved)
    assert len(applied) == 1                                     # the Ghost-only group dropped
    keys = [f["key"] for f in applied[0]["facets"]]
    assert keys == [t1.id]                                       # Curculionidae dropped, Carabidae kept


def test_reorder(session):
    t = _taxon(session, "Carabidae")
    a = ss.create(session, "A", _groups(t.id))
    b = ss.create(session, "B", _groups(t.id))
    c = ss.create(session, "C", _groups(t.id))
    ss.reorder(session, [c.id, a.id, b.id])
    assert [f.name for f in ss.list_searches(session)] == ["C", "A", "B"]
