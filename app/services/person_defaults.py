"""Service for the person_defaults table.

The table holds exactly one row with the two default person names used in
identifiedBy / recordedBy fields.  Both columns are nullable FK references
to person(full_name) with ON DELETE RESTRICT, so SQLite blocks deleting a
person who is set as a default, and merge_persons re-points them automatically
via the dynamic FK discovery in _fk_references_to_person.
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text


def get_defaults(session: Session) -> tuple[str | None, str | None]:
    """Return (default_identified_by, default_recorded_by)."""
    row = session.execute(
        text("SELECT default_identified_by, default_recorded_by FROM person_defaults")
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def set_defaults(
    session: Session,
    *,
    identified_by: str | None,
    recorded_by: str | None,
) -> None:
    """Overwrite the single person_defaults row.  Call inside an open transaction."""
    session.execute(
        text(
            "UPDATE person_defaults"
            " SET default_identified_by = :ib, default_recorded_by = :rb"
        ),
        {"ib": identified_by, "rb": recorded_by},
    )
    session.flush()
