"""Add dwc:acceptedNameUsageID to taxon (synonym link)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-04

dwc:acceptedNameUsageID — self-referential FK to the accepted taxon row.
Set only when dwc:taxonomicStatus = "synonym" or "invalid".
NULL on accepted taxa.

At DwC-A export time:
  dwc:acceptedNameUsageID → formatted as a local taxon identifier
  dwc:acceptedNameUsage   → derived from the referenced taxon's name fields (not stored)

DwC spec note: "the related record should exist locally within the same archive"
confirms this is a local FK, not an external URI.

ON DELETE RESTRICT: a taxon pointed to as the accepted name cannot be deleted
until all synonym links to it are cleared first.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        'ALTER TABLE taxon ADD COLUMN "dwc:acceptedNameUsageID" INTEGER '
        "REFERENCES taxon(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    op.execute('ALTER TABLE taxon DROP COLUMN "dwc:acceptedNameUsageID"')
