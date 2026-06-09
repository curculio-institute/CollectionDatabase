"""Rename dwc:occurrenceRemarks to dwc:materialEntityRemarks on collection_object.

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.alter_column(
                "dwc:occurrenceRemarks",
                new_column_name="dwc:materialEntityRemarks",
            )
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.alter_column(
                "dwc:materialEntityRemarks",
                new_column_name="dwc:occurrenceRemarks",
            )
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
