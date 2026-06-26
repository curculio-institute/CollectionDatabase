"""Repository (institution / collection) CRUD + label lookup (#56).

Keyed by ``collection_code`` (the prefix in every catalog number). The identifier
label resolves a code's prefix → ``collection_full_name`` via ``name_map``.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Repository
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
) -> Repository:
    r = Repository(
        collection_code=collection_code.strip(),
        collection_full_name=collection_full_name.strip(),
        institution_code=(institution_code or "").strip() or None,
        institution_full_name=(institution_full_name or "").strip() or None,
        taxonworks_institution_id=taxonworks_institution_id,
        taxonworks_collection_id=taxonworks_collection_id,
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
    r.updated_at = _utcnow()
    session.flush()
    return r


def delete_repository(session: Session, repo_id: int) -> None:
    r = session.get(Repository, repo_id)
    if r is not None:
        session.delete(r)
        session.flush()


def name_map(session: Session) -> dict[str, str]:
    """``{collection_code: collection_full_name}`` for the label resolver."""
    return {
        r.collection_code: r.collection_full_name
        for r in session.query(Repository).all()
    }
