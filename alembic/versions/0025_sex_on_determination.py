"""Move dwc:sex from collection_object to taxon_determination.

Sex is a property of the identification act, not of the physical object,
and is now printed on determination labels.

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-09
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add dwc:sex to taxon_determination — simple ADD COLUMN, no FK guard needed.
    with op.batch_alter_table("taxon_determination") as batch_op:
        batch_op.add_column(sa.Column("dwc:sex", sa.String, nullable=True))

    # Drop dwc:sex from collection_object.
    # Needs FK guard: child tables (taxon_determination, label_code, etc.)
    # reference collection_object(id) and batch recreate fails with FK ON.
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.drop_column("dwc:sex")
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    raise NotImplementedError("Downgrade from 0025 not implemented")
