"""Add ownerInstitutionCode to collection_object

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04

disposition (already present) tracks current state: "in collection", "on loan", etc.
ownerInstitutionCode tracks legal ownership, which differs from the holding institution
when specimens are loaned out or donated.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE collection_object ADD COLUMN ownerInstitutionCode TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE collection_object DROP COLUMN ownerInstitutionCode")
