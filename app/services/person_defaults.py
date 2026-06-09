"""Service for the person_defaults table.

The table holds exactly one row with the default person IDs for identifiedBy /
recordedBy fields.  Both columns are nullable FK references to person(id) with
ON DELETE RESTRICT, so SQLite blocks deleting a person who is set as a default,
and merge_persons re-points them automatically via the dynamic FK discovery in
_fk_references_to_person (which now looks for to_col == "id").
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text


def get_defaults(session: Session) -> tuple[str | None, str | None]:
    """Return (default_identified_by_name, default_recorded_by_name).

    Resolves the stored integer IDs to person names via JOIN so callers can
    display names directly without extra lookups.
    """
    row = session.execute(text(
        "SELECT p1.full_name, p2.full_name "
        "FROM person_defaults "
        "LEFT JOIN person p1 ON p1.id = person_defaults.default_identified_by_id "
        "LEFT JOIN person p2 ON p2.id = person_defaults.default_recorded_by_id"
    )).fetchone()
    return (row[0], row[1]) if row else (None, None)


def set_defaults(
    session: Session,
    *,
    identified_by_id: int | None,
    recorded_by_id: int | None,
) -> None:
    """Overwrite the single person_defaults row.  Call inside an open transaction."""
    session.execute(
        text(
            "UPDATE person_defaults "
            "SET default_identified_by_id = :ib, default_recorded_by_id = :rb"
        ),
        {"ib": identified_by_id, "rb": recorded_by_id},
    )
    session.flush()
