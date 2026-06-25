"""Generic single-name controlled vocabulary (app/services/vocab.py), exercised
through the preparation vocab + collection_object.preparation_id FK."""
import pytest

from app.models import CollectionObject, Preparation
from app.models.base import _utcnow
from app.services.vocabularies import preparation_vocab as V


def _co(session, *, catalog, preparation_id=None):
    co = CollectionObject(
        catalog_number=catalog, collection_code="Jilg", institution_code="Jilg",
        preparation_id=preparation_id, created_at=_utcnow(), updated_at=_utcnow(),
    )
    session.add(co)
    session.flush()
    return co


def test_get_or_create_is_idempotent(session):
    a = V.get_or_create(session, "pinned")
    b = V.get_or_create(session, "  pinned ")   # trimmed → same row
    assert a.id == b.id
    assert session.query(Preparation).filter_by(name="pinned").count() == 1


def test_options_maps_name_to_name(session):
    V.create(session, name="in ethanol")
    V.create(session, name="pinned")
    opts = V.options(session)
    assert opts == {"in ethanol": "in ethanol", "pinned": "pinned"}


def test_merge_repoints_fk_and_deletes_absorbed(session):
    keep = V.get_or_create(session, "pinned")
    absorb = V.get_or_create(session, "pin")          # a typo to fold in
    _co(session, catalog="A1", preparation_id=keep.id)
    _co(session, catalog="A2", preparation_id=absorb.id)
    _co(session, catalog="A3", preparation_id=absorb.id)

    preview = V.merge_preview(session, keep_id=keep.id, absorb_id=absorb.id)
    assert preview.reference_count == 2          # two specimens point at "pin"

    V.merge(session, keep_id=keep.id, absorb_id=absorb.id)
    assert session.get(Preparation, absorb.id) is None
    assert (session.query(CollectionObject)
            .filter_by(preparation_id=keep.id).count()) == 3


def test_delete_blocked_while_referenced(session):
    p = V.get_or_create(session, "in ethanol")
    _co(session, catalog="B1", preparation_id=p.id)
    with pytest.raises(ValueError):
        V.delete(session, p.id)
    assert session.get(Preparation, p.id) is not None   # still there


def test_delete_allowed_when_unreferenced(session):
    p = V.get_or_create(session, "carded")
    V.delete(session, p.id)
    assert session.get(Preparation, p.id) is None


def test_unique_name_enforced(session):
    from sqlalchemy.exc import IntegrityError
    V.create(session, name="pinned")
    with pytest.raises(IntegrityError):
        V.create(session, name="pinned")
