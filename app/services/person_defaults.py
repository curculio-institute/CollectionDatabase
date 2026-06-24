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


def get_defaults(session: Session) -> tuple[str | None, str | None, str | None]:
    """Return (default_identified_by_name, default_recorded_by_name,
    default_rights_holder_name).

    Resolves the stored integer IDs to person names via JOIN so callers can
    display names directly without extra lookups. Callers needing only the first
    two may keep indexing [0]/[1]; the third (media rightsHolder) was added with #48.
    """
    row = session.execute(text(
        "SELECT p1.full_name, p2.full_name, p3.full_name "
        "FROM person_defaults "
        "LEFT JOIN person p1 ON p1.id = person_defaults.default_identified_by_id "
        "LEFT JOIN person p2 ON p2.id = person_defaults.default_recorded_by_id "
        "LEFT JOIN person p3 ON p3.id = person_defaults.default_rights_holder_id"
    )).fetchone()
    return (row[0], row[1], row[2]) if row else (None, None, None)


def set_defaults(
    session: Session,
    *,
    identified_by_id: int | None,
    recorded_by_id: int | None,
    rights_holder_id: int | None = None,
) -> None:
    """Set the single person_defaults row.  Call inside an open transaction.

    Insert-or-update: the table is meant to hold exactly one row (seeded by
    migration 0022), but if that row is ever missing a plain UPDATE would
    silently no-op and the default would never persist. So insert when absent."""
    params = {"ib": identified_by_id, "rb": recorded_by_id, "rh": rights_holder_id}
    exists = session.execute(text("SELECT 1 FROM person_defaults LIMIT 1")).first()
    if exists is None:
        session.execute(
            text(
                "INSERT INTO person_defaults "
                "(default_identified_by_id, default_recorded_by_id, default_rights_holder_id) "
                "VALUES (:ib, :rb, :rh)"
            ),
            params,
        )
    else:
        session.execute(
            text(
                "UPDATE person_defaults SET "
                "default_identified_by_id = :ib, "
                "default_recorded_by_id = :rb, "
                "default_rights_holder_id = :rh"
            ),
            params,
        )
    session.flush()
