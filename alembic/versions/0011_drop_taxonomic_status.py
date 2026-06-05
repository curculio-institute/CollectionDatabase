"""Drop dwc:taxonomicStatus from taxon

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-04

Redundant with dwc:acceptedNameUsageID:
  IS NOT NULL  →  synonym  (export as taxonomicStatus="synonym")
  IS NULL      →  accepted (export as taxonomicStatus="accepted")
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('ALTER TABLE taxon DROP COLUMN "dwc:taxonomicStatus"')


def downgrade() -> None:
    op.execute('ALTER TABLE taxon ADD COLUMN "dwc:taxonomicStatus" TEXT')
