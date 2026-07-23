"""print_queue.taxon_determination_id — pin a reprinted determination row to a
specific identification (#38 reprint path)

A queued ``determination`` row references only the specimen, and the renderer
(`_co_to_det_label`) always composes the *current* determination. That is right
for the create paths (Mounting), but the Records reprint must reproduce **every**
identification a specimen carries, not just the current one. A nullable FK to
``taxon_determination`` lets a determination row name which identification it
prints; when NULL the renderer falls back to current, so every existing queue row
is unchanged.

``ON DELETE CASCADE``: deleting an identification removes any queued reprint of it
(the label you queued no longer exists). The exclusive-arc CHECK is untouched —
this column is an orthogonal refinement of a determination row, which still keeps
``collection_object_id`` set.

Uses SQLite's native ``ALTER TABLE ADD COLUMN`` (no table rebuild), so the STRICT
typing and both CHECK constraints on print_queue are preserved (DB-1 / migration
discipline — same approach as migration 0034's text_override column).

Revision ID: 0066
Revises: 0065
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite permits a *native* ADD COLUMN to carry an inline REFERENCES … ON DELETE
    # clause as long as the column is nullable with a NULL default — so the FK +
    # cascade are enforced at the DB level, with no table rebuild (STRICT + both
    # CHECK arcs preserved untouched, per migration discipline / DB-1). Alembic's
    # op.add_column tries to add the FK as a *separate* ALTER CONSTRAINT, which
    # SQLite cannot do without batch/rebuild — so issue the native statement raw.
    op.execute(
        "ALTER TABLE print_queue ADD COLUMN taxon_determination_id INTEGER "
        "REFERENCES taxon_determination(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.drop_column("print_queue", "taxon_determination_id")
