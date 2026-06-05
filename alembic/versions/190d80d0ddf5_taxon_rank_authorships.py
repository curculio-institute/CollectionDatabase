"""taxon_rank_authorships

Revision ID: 190d80d0ddf5
Revises: 7c70119a2132
Create Date: 2026-06-04 19:43:56.788765

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '190d80d0ddf5'
down_revision: Union[str, None] = '7c70119a2132'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLS = ["family", "subfamily", "tribe", "subtribe", "genus"]


def upgrade() -> None:
    with op.batch_alter_table("taxon") as b:
        for col in _COLS:
            b.add_column(sa.Column(f"{col}Authorship", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("taxon") as b:
        for col in _COLS:
            b.drop_column(f"{col}Authorship")
