"""Move dwc:typeStatus from collection_object to taxon_determination.

Type status identifies a specimen as a nomenclatural type for a specific
taxon, so it belongs with the determination, not the physical object.

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-09
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("taxon_determination") as batch_op:
        batch_op.add_column(sa.Column("dwc:typeStatus", sa.String, nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.drop_column("dwc:typeStatus")
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    raise NotImplementedError("Downgrade from 0026 not implemented")
