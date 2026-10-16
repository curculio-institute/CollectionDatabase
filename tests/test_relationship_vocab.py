"""Biological relationships become a managed vocabulary; the launch sync is gone
(spec "Removed: the launch sync", "Merge rules")."""
from pathlib import Path

import pytest

import app.services.biological as bio
import app.services.taxonworks as tw
from app.models.base import _utcnow
from app.models import BiologicalRelationship


def _rel(session, name, **kw):
    r = BiologicalRelationship(name=name, created_at=_utcnow(), updated_at=_utcnow(), **kw)
    session.add(r)
    session.flush()
    return r


def test_launch_sync_is_gone():
    assert not hasattr(bio, "sync_biological_relationships")
    assert not hasattr(tw, "fetch_biological_relationships")
    main_src = Path("app/ui/main.py").read_text(encoding="utf-8")
    assert "sync_biological_relationships" not in main_src


def test_create_relationship_refuses_case_insensitive_duplicate(session):
    bio.create_relationship(session, "feeds on")
    with pytest.raises(ValueError, match="already exists"):
        bio.create_relationship(session, "Feeds On")


def test_rename_refuses_case_insensitive_duplicate(session):
    bio.create_relationship(session, "feeds on")
    r = bio.create_relationship(session, "eats")
    with pytest.raises(ValueError, match="already exists"):
        bio.rename_relationship(session, r.id, "FEEDS ON")
    assert bio.rename_relationship(session, r.id, "eats on").name == "eats on"


def test_merge_refuses_a_linked_absorbed_relationship(session):
    keep = _rel(session, "feeds on")
    linked = _rel(session, "eats", taxonworks_id=11, tw_name="eats")
    with pytest.raises(ValueError, match="Unlink"):
        bio.merge_relationships(session, keep_id=keep.id, absorb_id=linked.id)


def test_merge_repoints_and_deletes(session):
    keep = _rel(session, "feeds on")
    absorb = _rel(session, "feeds-on")
    bio.merge_relationships(session, keep_id=keep.id, absorb_id=absorb.id)
    assert session.get(BiologicalRelationship, absorb.id) is None
