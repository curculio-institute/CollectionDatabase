"""Add CHECK constraints on collection_object.basisOfRecord and disposition.

basisOfRecord: 'PreservedSpecimen' | 'FossilSpecimen' | 'HumanObservation'.
  Note: TW's DwC importer only accepts PreservedSpecimen and FossilSpecimen;
  HumanObservation records cannot be synced via DwC (see §5b in CLAUDE.md).

disposition: closed local vocabulary.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-07
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # recreate="always" drops + recreates the table; SQLite won't drop
    # collection_object while child tables hold FK references, so we
    # temporarily suspend FK enforcement.
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_co_basis_of_record",
                "\"dwc:basisOfRecord\" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')",
            )
            batch_op.create_check_constraint(
                "ck_co_disposition",
                "\"dwc:disposition\" IS NULL OR \"dwc:disposition\" IN "
                "('in collection', 'on loan', 'donated', 'exchanged', 'missing', 'destroyed')",
            )
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collection_object", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_co_basis_of_record", type_="check")
            batch_op.drop_constraint("ck_co_disposition", type_="check")
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
