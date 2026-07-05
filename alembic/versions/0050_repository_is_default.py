"""repository.is_default — the user's own/home collection lives in the vocab (#83)

The default collection (used to stamp new specimens and generate catalog numbers) stops
being a free-text code in config.json and becomes a flag on the repositories vocab itself:
exactly one repository may be the default. This removes the silent-stub hole — digitize
reads the flagged repository and refuses to save if none is set, instead of
``resolve_id`` minting a placeholder from a config string.

- ``is_default INTEGER NOT NULL DEFAULT 0`` with a named column CHECK (native ADD COLUMN —
  no rebuild, so STRICT/UNIQUE on the STRICT repository table are preserved);
- a partial UNIQUE index so at most one repository is the default at a time.

Backfill: if exactly one repository exists, flag it (the single-collection case — preserves
current behaviour without reading runtime config). With 0 or many repositories none is
flagged and the user picks one in Settings.

Revision ID: 0050
Revises: 0049
"""
from typing import Union

from alembic import op

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE repository ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0 "
        "CONSTRAINT ck_repository_is_default CHECK (is_default IN (0, 1))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_repository_one_default "
        "ON repository (is_default) WHERE is_default = 1"
    )
    # Single-collection case: flag the lone repository as default (data-preserving).
    op.execute(
        "UPDATE repository SET is_default = 1 "
        "WHERE (SELECT COUNT(*) FROM repository) = 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_repository_one_default")
    op.execute("ALTER TABLE repository DROP COLUMN is_default")
