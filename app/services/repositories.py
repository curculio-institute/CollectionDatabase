"""Repository (institution / collection) CRUD + label lookup (#56).

Keyed by ``collection_code`` (the prefix in every catalog number). The identifier
label resolves a code's prefix → ``collection_full_name`` via ``name_map``.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CollectionObject, Repository
from app.models.base import _utcnow


def list_repositories(session: Session) -> list[Repository]:
    return session.query(Repository).order_by(Repository.collection_code).all()


def create_repository(
    session: Session,
    *,
    collection_code: str,
    collection_full_name: str,
    institution_code: str | None = None,
    institution_full_name: str | None = None,
    taxonworks_institution_id: int | None = None,
    taxonworks_collection_id: int | None = None,
    person_id: int | None = None,
) -> Repository:
    r = Repository(
        collection_code=collection_code.strip(),
        collection_full_name=collection_full_name.strip(),
        institution_code=(institution_code or "").strip() or None,
        institution_full_name=(institution_full_name or "").strip() or None,
        taxonworks_institution_id=taxonworks_institution_id,
        taxonworks_collection_id=taxonworks_collection_id,
        person_id=person_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(r)
    session.flush()
    return r


def update_repository(
    session: Session,
    repo_id: int,
    *,
    collection_code: str,
    collection_full_name: str,
    institution_code: str | None = None,
    institution_full_name: str | None = None,
    taxonworks_institution_id: int | None = None,
    taxonworks_collection_id: int | None = None,
    person_id: int | None = None,
) -> Repository:
    r = session.get(Repository, repo_id)
    if r is None:
        raise ValueError(f"Repository {repo_id} not found")
    r.collection_code = collection_code.strip()
    r.collection_full_name = collection_full_name.strip()
    r.institution_code = (institution_code or "").strip() or None
    r.institution_full_name = (institution_full_name or "").strip() or None
    r.taxonworks_institution_id = taxonworks_institution_id
    r.taxonworks_collection_id = taxonworks_collection_id
    r.person_id = person_id
    r.updated_at = _utcnow()
    session.flush()
    return r


def delete_repository(session: Session, repo_id: int) -> None:
    """Delete a collection. Blocked while any specimen still belongs to it (#72).

    The ``collection_object.repository_id`` FK is ON DELETE RESTRICT, so the DB
    blocks this anyway; the count check turns the raw IntegrityError into a friendly
    message (mirrors persons.delete_person / vocab.Vocabulary.delete).
    """
    r = session.get(Repository, repo_id)
    if r is None:
        return
    n = (
        session.query(CollectionObject)
        .filter(CollectionObject.repository_id == repo_id)
        .count()
    )
    if n:
        raise ValueError(
            f"Cannot delete collection {r.collection_code!r}: {n} specimen(s) still "
            f"belong to it. Reassign them to another collection first."
        )
    session.delete(r)
    session.flush()


def resolve_id(
    session: Session,
    *,
    collection_code: str,
    institution_code: str | None = None,
    collection_full_name: str | None = None,
) -> int:
    """Resolve a collection code to its repository id, creating the row if absent.

    The save-time seam for ``collection_object.repository_id`` (#75): the UI carries
    a collection-code string (config-backed for the user's own collection, typed for
    a host/other collection), and this get-or-creates the matching repository inside
    the caller's transaction — mirroring person / vocab ``commit(session)`` resolution.
    ``collection_code`` is required (NOT NULL on the FK target); a blank one is refused
    loudly rather than silently defaulted.
    """
    code = (collection_code or "").strip()
    if not code:
        raise ValueError("collection_code is required to resolve a repository")
    r = (
        session.query(Repository)
        .filter(Repository.collection_code == code)
        .one_or_none()
    )
    if r is None:
        r = create_repository(
            session,
            collection_code=code,
            collection_full_name=(collection_full_name or "").strip() or code,
            institution_code=institution_code,
        )
    return r.id


def get_default(session: Session) -> Repository | None:
    """The repository flagged as the user's default/home collection, or None (#83).

    The default lives on the vocab (``repository.is_default``), not as a code string in
    config.json — so digitize derives both the catalog-number prefix and ``repository_id``
    from one chosen row, and there is no string to silently stub a placeholder from.
    """
    return (
        session.query(Repository)
        .filter(Repository.is_default == 1)
        .one_or_none()
    )


def set_default(session: Session, repo_id: int) -> None:
    """Make ``repo_id`` the sole default collection. Clears the old default first so the
    partial-unique ``one default`` index never trips mid-statement."""
    if session.get(Repository, repo_id) is None:
        raise ValueError(f"Repository {repo_id} not found")
    now = _utcnow()
    session.query(Repository).filter(Repository.is_default == 1).update(
        {"is_default": 0, "updated_at": now})
    session.query(Repository).filter(Repository.id == repo_id).update(
        {"is_default": 1, "updated_at": now})
    session.flush()


def name_map(session: Session) -> dict[str, str]:
    """``{collection_code: collection_full_name}`` for the label resolver."""
    return {
        r.collection_code: r.collection_full_name
        for r in session.query(Repository).all()
    }
