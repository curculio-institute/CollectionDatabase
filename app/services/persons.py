from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.base import _utcnow


def list_persons(session: Session) -> list[Person]:
    return session.query(Person).order_by(Person.full_name).all()


def person_options(session: Session) -> dict[str, str]:
    """Return {full_name: full_name} for use in ui.select.

    Key and label are both the full name — abbreviated name never appears in forms.
    Abbreviated name is accessible via Person.label_name for label printing only.
    """
    return {p.dwc_name: p.display_label for p in list_persons(session)}


def get_or_create_person(
    session: Session,
    *,
    full_name: str,
) -> Person:
    """Return the existing Person with this name, or create a new one.

    Safe to call multiple times within the same transaction — session.flush()
    inside create_person makes the new row visible to subsequent queries in the
    same session, so a second call with the same name finds the already-flushed
    row instead of hitting the unique constraint.
    """
    existing = session.query(Person).filter_by(full_name=full_name.strip()).first()
    if existing:
        return existing
    return create_person(session, full_name=full_name)


def create_person(
    session: Session,
    *,
    full_name: str,
    abbreviated_name: str | None = None,
    orcid: str | None = None,
) -> Person:
    p = Person(
        full_name=full_name.strip(),
        abbreviated_name=abbreviated_name.strip() if abbreviated_name else None,
        orcid=orcid.strip() if orcid else None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(p)
    session.flush()
    return p


def update_person(
    session: Session,
    person_id: int,
    *,
    full_name: str,
    abbreviated_name: str | None = None,
    orcid: str | None = None,
) -> Person:
    p = session.get(Person, person_id)
    if p is None:
        raise ValueError(f"Person {person_id} not found")
    p.full_name        = full_name.strip()
    p.abbreviated_name = abbreviated_name.strip() if abbreviated_name else None
    p.orcid            = orcid.strip() if orcid else None
    p.updated_at       = _utcnow()
    session.flush()
    return p


def delete_person(session: Session, person_id: int) -> None:
    p = session.get(Person, person_id)
    if p:
        session.delete(p)
        session.flush()
