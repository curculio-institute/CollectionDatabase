"""label_batch

Revision ID: cc96166a829c
Revises: f76a2535f212
Create Date: 2026-06-04 21:56:20.331190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc96166a829c'
down_revision: Union[str, None] = 'f76a2535f212'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS label_batch (
            id         INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT
    """)
    # Add batch_id only if not already present
    from sqlalchemy import inspect
    conn = op.get_bind()
    cols = [c["name"] for c in inspect(conn).get_columns("label_code")]
    if "batch_id" not in cols:
        with op.batch_alter_table("label_code") as b:
            b.add_column(sa.Column("batch_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("label_code") as b:
        b.drop_column("batch_id")
    op.drop_table("label_batch")
