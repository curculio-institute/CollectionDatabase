"""A collection can carry an optional contact/owner person (#79).

`repository.person_id` is a nullable FK → person(id) ON DELETE RESTRICT. Merge/delete
of a person re-point/block this FK dynamically (PRAGMA foreign_key_list), same as every
other person FK — so a person referenced by a collection cannot be deleted.
"""
import pytest

from app.models.person import Person
from app.models.base import _utcnow
import app.services.repositories as repo_svc
import app.services.persons as persons_svc


def _person(session, name: str) -> Person:
    p = Person(full_name=name, created_at=_utcnow(), updated_at=_utcnow())
    session.add(p)
    session.flush()
    return p


def test_create_repository_with_person(session):
    greg = _person(session, "Greg")
    r = repo_svc.create_repository(
        session, collection_code="GC", collection_full_name="Greg's Collection",
        person_id=greg.id)
    assert r.person_id == greg.id


def test_person_defaults_to_none(session):
    r = repo_svc.create_repository(
        session, collection_code="AC", collection_full_name="Anon Collection")
    assert r.person_id is None


def test_update_repository_sets_and_clears_person(session):
    greg = _person(session, "Greg")
    r = repo_svc.create_repository(
        session, collection_code="GC", collection_full_name="Greg's Collection")
    repo_svc.update_repository(
        session, r.id, collection_code="GC",
        collection_full_name="Greg's Collection", person_id=greg.id)
    assert session.get(repo_svc.Repository, r.id).person_id == greg.id
    # clear it again
    repo_svc.update_repository(
        session, r.id, collection_code="GC",
        collection_full_name="Greg's Collection", person_id=None)
    assert session.get(repo_svc.Repository, r.id).person_id is None


def test_delete_person_blocked_while_collection_references_them(session):
    greg = _person(session, "Greg")
    repo_svc.create_repository(
        session, collection_code="GC", collection_full_name="Greg's Collection",
        person_id=greg.id)
    with pytest.raises(Exception):
        persons_svc.delete_person(session, greg.id)


def test_merge_persons_repoints_collection_owner(session):
    greg = _person(session, "Greg")
    greg2 = _person(session, "Gregory")
    r = repo_svc.create_repository(
        session, collection_code="GC", collection_full_name="Greg's Collection",
        person_id=greg2.id)
    # merge the duplicate (greg2) into the canonical (greg)
    persons_svc.merge_persons(session, keep_id=greg.id, absorb_id=greg2.id)
    session.expire_all()  # merge re-points FKs via raw SQL; drop the stale ORM cache
    assert session.get(repo_svc.Repository, r.id).person_id == greg.id
