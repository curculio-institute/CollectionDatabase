"""Add CHECK constraints to print_queue: label_type enum + exclusive-arc FK.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-07
"""
from __future__ import annotations
from typing import Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("print_queue", recreate="always") as batch_op:
        batch_op.create_check_constraint(
            "ck_print_queue_label_type",
            "label_type IN ('data', 'determination', 'identifier')",
        )
        batch_op.create_check_constraint(
            "ck_print_queue_exclusive_arc",
            "(label_type IN ('data', 'determination')"
            "  AND collection_object_id IS NOT NULL"
            "  AND label_code_id IS NULL)"
            " OR "
            "(label_type = 'identifier'"
            "  AND label_code_id IS NOT NULL"
            "  AND collection_object_id IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("print_queue", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_print_queue_label_type", type_="check")
        batch_op.drop_constraint("ck_print_queue_exclusive_arc", type_="check")
