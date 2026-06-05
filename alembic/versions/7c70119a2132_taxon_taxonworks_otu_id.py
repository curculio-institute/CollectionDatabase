"""taxon_taxonworks_otu_id

Revision ID: 7c70119a2132
Revises: 0011
Create Date: 2026-06-04 19:25:55.814314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c70119a2132'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("taxon") as batch_op:
        batch_op.add_column(sa.Column("taxonworksOtuID", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("taxon") as batch_op:
        batch_op.drop_column("taxonworksOtuID")
