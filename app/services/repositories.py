"""Repository (institution / collection) CRUD + label lookup (#56).

A collection is identified by its **name** (``collection_full_name``, UNIQUE —
migration 0072): a private collection often has no code at all. ``collection_code`` is
optional and unique where present; it is the prefix in the own collection's catalog
numbers, which the identifier label resolves → ``collection_full_name`` via ``name_map``.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CollectionObject, Repository
from app.models.base import _utcnow


def list_repositories(session: Session) -> list[Repository]:
    """All collections — coded ones first by code, then the codeless ones by name."""
    return (
        session.query(Repository)
        .order_by(Repository.collection_code.is_(None),
                  Repository.collection_code, Repository.collection_full_name)
        .all()
    )


def display_label(repo: Repository) -> str:
    """The one human rendering of a collection: ``JJPC — Jakob Jilg collection`` with a
    code, ``Frank Lange collection`` without. Never a bare code — the name is the
    collection's identity and is always present."""
    code = (repo.collection_code or "").strip()
    name = (repo.collection_full_name or "").strip()
    if code and name and code != name:
        return f"{code} — {name}"
    return name or code


def _clean_identity(session: Session, collection_code: str | None,
                    collection_full_name: str | None, *,
                    exclude_id: int | None = None) -> tuple[str | None, str]:
    """Normalise + vet a collection's (code, name), raising a friendly ValueError where
    the DB would otherwise answer with a raw IntegrityError: the name is required and
    unique; the code is optional and unique where present."""
    code = (collection_code or "").strip() or None
    name = (collection_full_name or "").strip()
    if not name:
        raise ValueError("A collection needs a name.")
    q = session.query(Repository).filter(Repository.collection_full_name == name)
    if exclude_id is not None:
        q = q.filter(Repository.id != exclude_id)
    if q.first() is not None:
        raise ValueError(f"A collection named {name!r} already exists.")
    if code is not None:
        q = session.query(Repository).filter(Repository.collection_code == code)
        if exclude_id is not None:
            q = q.filter(Repository.id != exclude_id)
        other = q.first()
        if other is not None:
            raise ValueError(
                f"The collection code {code!r} is already used by "
                f"{other.collection_full_name!r}.")
    return code, name


def create_repository(
    session: Session,
    *,
    collection_full_name: str,
    collection_code: str | None = None,
    institution_code: str | None = None,
    institution_full_name: str | None = None,
    taxonworks_institution_id: int | None = None,
    taxonworks_collection_id: int | None = None,
    person_id: int | None = None,
) -> Repository:
    code, name = _clean_identity(session, collection_code, collection_full_name)
    r = Repository(
        collection_code=code,
        collection_full_name=name,
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
    collection_full_name: str,
    collection_code: str | None = None,
    institution_code: str | None = None,
    institution_full_name: str | None = None,
    taxonworks_institution_id: int | None = None,
    taxonworks_collection_id: int | None = None,
    person_id: int | None = None,
) -> Repository:
    r = session.get(Repository, repo_id)
    if r is None:
        raise ValueError(f"Repository {repo_id} not found")
    code, name = _clean_identity(session, collection_code, collection_full_name,
                                 exclude_id=repo_id)
    if code is None and r.is_default:
        raise ValueError(
            "The default collection needs a collection code — it is the prefix of "
            "your catalog numbers.")
    r.collection_code = code
    r.collection_full_name = name
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
            f"Cannot delete collection {display_label(r)!r}: {n} specimen(s) still "
            f"belong to it. Reassign them to another collection first."
        )
    session.delete(r)
    session.flush()


def get_or_create_by_name(session: Session, name: str) -> Repository:
    """The collection named ``name``, creating a name-only row if there is none.

    The save-time seam for the collection picker's ``✚ add`` (mirrors person / vocab
    ``commit(session)``): a foreign collection is identified by its name, and its codes
    — if it has any — are added later in Controlled Vocabularies. Nothing is guessed:
    a new row gets no code. A blank name is refused loudly.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A collection name is required.")
    r = (
        session.query(Repository)
        .filter(Repository.collection_full_name == name)
        .one_or_none()
    )
    if r is None:
        r = create_repository(session, collection_full_name=name)
    return r


def count_without_catalog_number(session: Session, repo_id: int) -> int:
    """How many specimens of this collection have no catalog number."""
    return (
        session.query(CollectionObject)
        .filter(CollectionObject.repository_id == repo_id,
                CollectionObject.catalog_number.is_(None))
        .count()
    )


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
    r = session.get(Repository, repo_id)
    if r is None:
        raise ValueError(f"Repository {repo_id} not found")
    # Both are DB-enforced too (ck_repository_default_has_code and
    # trg_repository_default_requires_catalog_numbers); these say why.
    if not r.collection_code:
        raise ValueError(
            f"{r.collection_full_name!r} has no collection code, so it cannot be the "
            "default collection — the code is the prefix of your catalog numbers.")
    n = count_without_catalog_number(session, repo_id)
    if n:
        raise ValueError(
            f"{display_label(r)!r} holds {n} specimen(s) without a catalog number, so "
            "it cannot be the default collection — every specimen in your own "
            "collection must have one.")
    now = _utcnow()
    session.query(Repository).filter(Repository.is_default == 1).update(
        {"is_default": 0, "updated_at": now})
    session.query(Repository).filter(Repository.id == repo_id).update(
        {"is_default": 1, "updated_at": now})
    session.flush()


def name_map(session: Session) -> dict[str, str]:
    """``{collection_code: collection_full_name}`` for the label resolver. Codeless
    collections have no prefix to resolve, so they are not in the map."""
    return {
        r.collection_code: r.collection_full_name
        for r in session.query(Repository).filter(Repository.collection_code.isnot(None)).all()
    }
