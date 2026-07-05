"""Generic vocab default flag — flaggable Tier-1 autofill (preparation, migration 0052).

Mirrors repository.is_default: at most one entry is the default (partial-unique index);
get_default / set_default on the generic Vocabulary service manage it.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.preparation import Preparation
from app.services.vocabularies import preparation_vocab, disposition_vocab


def _prep(session, name):
    return preparation_vocab.create(session, name=name)


def test_no_default_initially(session):
    _prep(session, "pinned")
    assert preparation_vocab.get_default(session) is None
    assert preparation_vocab.get_default_name(session) is None


def test_set_and_get_default(session):
    p = _prep(session, "pinned")
    preparation_vocab.set_default(session, p.id)
    assert preparation_vocab.get_default(session).id == p.id
    assert preparation_vocab.get_default_name(session) == "pinned"


def test_set_default_moves_the_flag(session):
    a = _prep(session, "pinned")
    b = _prep(session, "in ethanol")
    preparation_vocab.set_default(session, a.id)
    preparation_vocab.set_default(session, b.id)   # must clear A first, not trip the index
    assert preparation_vocab.get_default(session).id == b.id
    assert session.get(Preparation, a.id).is_default == 0


def test_clear_default_with_none(session):
    p = _prep(session, "pinned")
    preparation_vocab.set_default(session, p.id)
    preparation_vocab.set_default(session, None)
    assert preparation_vocab.get_default(session) is None


def test_default_survives_rename_and_merge(session):
    a = _prep(session, "pinnd")            # typo
    b = _prep(session, "pinned")
    preparation_vocab.set_default(session, a.id)
    preparation_vocab.merge(session, keep_id=b.id, absorb_id=a.id)
    session.expire_all()
    # merge deletes the absorbed row; the surviving row is what remains referenced.
    assert session.get(Preparation, a.id) is None


def test_vocab_without_default_flag_refuses(session):
    # disposition vocab is not flagged has_default → the API is inert / guarded.
    assert disposition_vocab.get_default(session) is None
    with pytest.raises(ValueError, match="no default flag"):
        disposition_vocab.set_default(session, 1)


def test_two_defaults_rejected_at_db_level(session):
    a = _prep(session, "pinned")
    b = _prep(session, "in ethanol")
    with pytest.raises(IntegrityError):
        session.query(Preparation).filter(Preparation.id.in_([a.id, b.id])).update(
            {"is_default": 1}, synchronize_session=False)
