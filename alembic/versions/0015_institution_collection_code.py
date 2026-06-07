"""Replace ownerInstitutionCode with institutionCode/collectionCode

- Drop dwc:ownerInstitutionCode (not mapped by TaxonWorks DwC importer; occurrence.rb:728)
- Rename catalogNamespace → dwc:collectionCode (maps to TW catalog-number namespace lookup)

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table on SQLite recreates the table (copy-drop-rename). Temporarily
    # disable FK enforcement so the DROP TABLE step does not fail on referencing tables.
    op.execute("PRAGMA foreign_keys = OFF")
    with op.batch_alter_table("collection_object") as batch_op:
        batch_op.drop_column("dwc:ownerInstitutionCode")
        batch_op.alter_column(
            "catalogNamespace",
            new_column_name="dwc:collectionCode",
            existing_type=sa.String(),
            existing_nullable=False,
        )
    op.execute("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys = OFF")
    with op.batch_alter_table("collection_object") as batch_op:
        batch_op.alter_column(
            "dwc:collectionCode",
            new_column_name="catalogNamespace",
            existing_type=sa.String(),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column("dwc:ownerInstitutionCode", sa.String(), nullable=True)
        )
    op.execute("PRAGMA foreign_keys = ON")
