"""taxon_subgenus_authorship

Revision ID: d67db47af8e8
Revises: 190d80d0ddf5
Create Date: 2026-06-04 19:51:52.053566

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd67db47af8e8'
down_revision: Union[str, None] = '190d80d0ddf5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("taxon") as b:
        b.add_column(sa.Column("subgenusAuthorship", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("taxon") as b:
        b.drop_column("subgenusAuthorship")
