"""saved_search — Explore favorites (saved searches) (#137)

A favorite is a named snapshot of the Explore search state — the stacked-group
structure `[{op, facets:[…]}]` — stored as a JSON payload. It references DB entities
(taxon_id, repository_id, geo-vocab id, person), so per the DB-entity-reference rule it
lives in the DB, not config.json (which would also dangle after a DB wipe).

One STRICT table, created fresh (no rebuild), so STRICT + every CHECK/UNIQUE is declared
here directly (DB-1 discipline). `is_default` marks the search auto-applied when Explore
opens; a partial-unique index keeps at most one, mirroring repository.is_default (#83).
Downgrade drops the table.

Revision ID: 0065
Revises: 0064
"""
from typing import Union

from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE saved_search (
            id          INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            payload     TEXT    NOT NULL,
            is_default  INTEGER NOT NULL DEFAULT 0,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            CONSTRAINT uq_saved_search_name       UNIQUE (name),
            CONSTRAINT ck_saved_search_is_default CHECK (is_default IN (0, 1))
        ) STRICT
    """)
    # At most one default search at a time (partial unique index, mirrors repository #83).
    op.execute(
        "CREATE UNIQUE INDEX uq_saved_search_one_default "
        "ON saved_search (is_default) WHERE is_default = 1")


def downgrade() -> None:
    op.execute("DROP TABLE saved_search")
