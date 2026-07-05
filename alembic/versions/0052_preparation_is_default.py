"""preparation.is_default — a flaggable Tier-1 autofill default preparation (parent #72 follow-up)

One preparation can be flagged as the create-time default: new specimens (Digitize
standard/visiting, Import & Assign) pre-fill their preparations field with it (still
editable — true Tier-1). Data-driven via the vocab, mirroring repository.is_default (#83),
rather than a hardcoded constant.

- ``is_default INTEGER NOT NULL DEFAULT 0`` with a named column CHECK (native ADD COLUMN —
  no rebuild, so STRICT/UNIQUE on the STRICT preparation table are preserved);
- a partial UNIQUE index so at most one preparation is the default at a time.

No backfill — a fresh/empty vocab has no default until the user flags one (Digitize then
starts the field empty, exactly as before).

Revision ID: 0052
Revises: 0051
"""
from typing import Union

from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE preparation ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0 "
        "CONSTRAINT ck_preparation_is_default CHECK (is_default IN (0, 1))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_preparation_one_default "
        "ON preparation (is_default) WHERE is_default = 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_preparation_one_default")
    op.execute("ALTER TABLE preparation DROP COLUMN is_default")
