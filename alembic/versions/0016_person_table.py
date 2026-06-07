"""person table for controlled vocabulary of collectors / identifiers

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("id",               sa.Integer(), nullable=False, primary_key=True),
        sa.Column("full_name",        sa.String(),  nullable=False),
        sa.Column("abbreviated_name", sa.String(),  nullable=True),
        sa.Column("orcid",            sa.String(),  nullable=True),
        sa.Column("created_at",       sa.String(),  nullable=False),
        sa.Column("updated_at",       sa.String(),  nullable=False),
        sa.UniqueConstraint("full_name", name="uq_person_full_name"),
    )


def downgrade() -> None:
    op.drop_table("person")
