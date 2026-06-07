"""Add CHECK constraint on label_code.status.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-07
"""
from __future__ import annotations
from typing import Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("label_code", recreate="always") as batch_op:
        batch_op.create_check_constraint(
            "ck_label_code_status",
            "status IN ('reserved', 'assigned')",
        )


def downgrade() -> None:
    with op.batch_alter_table("label_code", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_label_code_status", type_="check")
