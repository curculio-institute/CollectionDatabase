"""One current determination per specimen (#74)

A specimen may have at most one identification with is_current=1; older
determinations are kept as history with is_current=0. Enforced by a partial
UNIQUE index on collection_object_id WHERE is_current = 1, so the unbounded
history rows stay unconstrained. No table rebuild (the index is a separate
object; STRICT/CHECK/FK on taxon_determination are untouched).

Revision ID: 0046
Revises: 0045
"""
from typing import Union

from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_td_one_current_per_co "
        "ON taxon_determination (collection_object_id) WHERE is_current = 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_td_one_current_per_co")
