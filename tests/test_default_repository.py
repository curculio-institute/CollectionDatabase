"""Default-collection flag on the repositories vocab (#83).

The user's own/home collection is a row flagged ``repository.is_default`` — not a code
string in config.json. At most one row may be the default (partial-unique index), and the
own-collection digitize paths read it via ``get_default`` instead of stubbing a placeholder.
"""
import pytest
from sqlalchemy.exc import IntegrityError

import app.services.repositories as repo_svc
from tests.helpers import ensure_repo


def test_get_default_none_when_unset(session):
    ensure_repo(session, "DOE")          # a repository exists, but none is flagged
    assert repo_svc.get_default(session) is None


def test_set_default_flags_repository(session):
    rid = ensure_repo(session, "DOE")
    repo_svc.set_default(session, rid)
    d = repo_svc.get_default(session)
    assert d is not None and d.id == rid and d.is_default == 1


def test_set_default_moves_the_flag(session):
    a = ensure_repo(session, "DOE")
    b = ensure_repo(session, "ACME")
    repo_svc.set_default(session, a)
    repo_svc.set_default(session, b)     # must clear A first, not trip the unique index
    assert repo_svc.get_default(session).id == b
    assert session.get(repo_svc.Repository, a).is_default == 0


def test_set_default_unknown_id_raises(session):
    with pytest.raises(ValueError):
        repo_svc.set_default(session, 999999)


def test_two_defaults_rejected_at_db_level(session):
    """The partial-unique index is the DB backstop behind set_default's clear-first."""
    a = ensure_repo(session, "DOE")
    b = ensure_repo(session, "ACME")
    with pytest.raises(IntegrityError):
        session.query(repo_svc.Repository).filter(
            repo_svc.Repository.id.in_([a, b])).update(
            {"is_default": 1}, synchronize_session=False)
