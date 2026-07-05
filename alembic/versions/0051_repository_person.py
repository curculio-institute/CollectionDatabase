"""repository.person_id — associate a contact/owner person with a collection (#79)

The user wants to tie a person to a collection (e.g. "Greg's Collection" / code GC →
Greg). No roles — a single optional contact/owner person per repository.

- ``person_id INTEGER REFERENCES person(id) ON DELETE RESTRICT``, nullable (native ADD
  COLUMN with a NULL default — SQLite permits a REFERENCES clause on ADD COLUMN only when
  the default is NULL, so no table rebuild and STRICT/UNIQUE/CHECK on the STRICT repository
  table are preserved).

``merge_persons`` / ``persons.delete_person`` re-discover FK references to ``person``
dynamically via ``PRAGMA foreign_key_list``, so merging re-points this column and deleting
a referenced person is blocked (ON DELETE RESTRICT) with no extra wiring.

Revision ID: 0051
Revises: 0050
"""
from typing import Union

from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE repository ADD COLUMN person_id INTEGER "
        "REFERENCES person(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE repository DROP COLUMN person_id")
