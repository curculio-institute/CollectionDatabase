"""collecting_event island

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("collecting_event") as batch_op:
        batch_op.add_column(
            sa.Column("dwc:island", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("collecting_event") as batch_op:
        batch_op.drop_column("dwc:island")
