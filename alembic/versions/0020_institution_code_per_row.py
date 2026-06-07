"""Add dwc:institutionCode NOT NULL column to collection_object.

institutionCode is stored per row (like collectionCode) so that specimens
from guest collections can carry their own institution code. The value is
configured in Settings and written at record-creation time; it is treated
as immutable after creation (same guard as catalogNumber / collectionCode).

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-08
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN works natively in SQLite when a DEFAULT is provided.
    # No recreate needed; existing rows get server_default "".
    with op.batch_alter_table("collection_object") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dwc:institutionCode",
                sa.String,
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("collection_object") as batch_op:
        batch_op.drop_column("dwc:institutionCode")
