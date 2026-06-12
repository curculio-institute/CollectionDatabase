"""Add grouping to print_queue: print_group_id + source.

A print run is laid out as groups, one per "queue addition" (e.g. one Mounting
Session save, or one batch of reserved identifier codes). Each group prints with
a small origin header and its labels arranged so corresponding labels stay
adjacent for cut-and-match. The queue is ephemeral (rows are cleared after a
print run), so the group marker is stored denormalised on each row rather than
in a separate table.

  print_group_id : INTEGER  — id shared by all rows enqueued in one operation
  source         : TEXT     — origin header, e.g. "Mounting Session"

Both nullable: existing/legacy rows (NULL group) render as one fallback group.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-12
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("print_queue") as batch_op:
        batch_op.add_column(sa.Column("print_group_id", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("source", sa.String, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("print_queue") as batch_op:
        batch_op.drop_column("source")
        batch_op.drop_column("print_group_id")
