"""collection_object.dwc:otherCatalogNumbers — prior catalog numbers (#77, parent #72)

A free-text DwC field for catalog numbers a specimen carried at *previous* owning
institutions (collections acquire specimens from other collections). Free text (no
controlled vocab, no CHECK) — it's a list-ish DwC term. Previous *institutions* are
deliberately NOT recorded; only the prior numbers.

Native ADD COLUMN (no table rebuild → STRICT typing + existing CHECK/UNIQUE/FK on
collection_object are preserved; CLAUDE.md migration discipline).

Revision ID: 0049
Revises: 0048
"""
from typing import Union

from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE collection_object ADD COLUMN "dwc:otherCatalogNumbers" TEXT')


def downgrade() -> None:
    op.execute('ALTER TABLE collection_object DROP COLUMN "dwc:otherCatalogNumbers"')
