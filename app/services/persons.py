from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.base import _utcnow

# Human-readable label for each FK column that references person(id).
_FK_LABELS: dict[tuple[str, str], str] = {
    ("collecting_event",   "recorded_by_id"):          "recorded-by on collecting events",
    ("taxon_determination","identified_by_id"):         "identified-by on determinations",
    ("person_defaults",    "default_identified_by_id"): "set as default identified-by",
    ("person_defaults",    "default_recorded_by_id"):   "set as default recorded-by",
}


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
    if p is None:
        return
    blocking = _blocking_description(session, person_id)
    if blocking:
        raise ValueError(f"Cannot delete '{p.full_name}': still used as {blocking}.")
    session.delete(p)
    session.flush()


@dataclass(frozen=True)
class MergePreview:
    keep_id: int
    keep_name: str
    absorb_id: int
    absorb_name: str
    reference_count: int   # total rows that will be re-pointed


def merge_preview(session: Session, keep_id: int, absorb_id: int) -> MergePreview:
    """Return a preview of what a merge would do without committing anything."""
    keep   = session.get(Person, keep_id)
    absorb = session.get(Person, absorb_id)
    if keep is None or absorb is None:
        raise ValueError("One or both persons not found")

    count = _count_references(session, absorb_id)
    return MergePreview(
        keep_id=keep_id,
        keep_name=keep.full_name,
        absorb_id=absorb_id,
        absorb_name=absorb.full_name,
        reference_count=count,
    )


def merge_persons(session: Session, keep_id: int, absorb_id: int) -> None:
    """Re-point every FK reference from absorb → keep, then delete absorb.

    Uses PRAGMA foreign_key_list to discover referencing columns dynamically —
    no hardcoded table/column list needed.
    """
    if keep_id == absorb_id:
        raise ValueError("Cannot merge a person with themselves")

    keep   = session.get(Person, keep_id)
    absorb = session.get(Person, absorb_id)
    if keep is None or absorb is None:
        raise ValueError("One or both persons not found")

    for table, col in _fk_references_to_person(session):
        session.execute(
            text(f'UPDATE "{table}" SET "{col}" = :keep WHERE "{col}" = :absorb'),
            {"keep": keep_id, "absorb": absorb_id},
        )

    session.delete(absorb)
    session.flush()


# ── internal helpers ─────────────────────────────────────────────────────────

def _all_user_tables(session: Session) -> list[str]:
    rows = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'")
    ).fetchall()
    return [r[0] for r in rows]


def _fk_references_to_person(session: Session) -> list[tuple[str, str]]:
    """Return [(table, column), …] for every FK column that references person(id)."""
    refs = []
    for table in _all_user_tables(session):
        fk_rows = session.execute(
            text(f"PRAGMA foreign_key_list(\"{table}\")")
        ).fetchall()
        for fk in fk_rows:
            # PRAGMA columns: id, seq, table, from, to, on_update, on_delete, match
            ref_table = fk[2]
            from_col  = fk[3]
            to_col    = fk[4]
            if ref_table == "person" and to_col == "id":
                refs.append((table, from_col))
    return refs


def _blocking_description(session: Session, person_id: int) -> str:
    """Return a human-readable string describing what still references this person.

    Returns empty string when no references exist (delete is safe).
    """
    parts: list[str] = []
    for table, col in _fk_references_to_person(session):
        count = session.execute(
            text(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" = :id'),
            {"id": person_id},
        ).scalar()
        if count:
            label = _FK_LABELS.get((table, col), f"{col} in {table}")
            parts.append(f"{label} ({count})" if count > 1 else label)
    return ", ".join(parts)


def _count_references(session: Session, person_id: int) -> int:
    total = 0
    for table, col in _fk_references_to_person(session):
        row = session.execute(
            text(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" = :id'),
            {"id": person_id},
        ).fetchone()
        total += row[0]
    return total
