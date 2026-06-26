"""taxon.sort_order — display-only manual ordering of taxa (#40)

A nullable integer used ONLY by the checklist UI to hold the collection's taxonomic
sequence among siblings (family-and-above ranks; below family stays alphabetical).
NULL ⇒ alphabetical. Never exported (not a DwC term). Native ADD COLUMN, no rebuild.

Revision ID: 0042
Revises: 0041
"""
from typing import Union

from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE taxon ADD COLUMN sort_order INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE taxon DROP COLUMN sort_order")
