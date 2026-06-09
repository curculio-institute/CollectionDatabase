"""Drop legacy text FK columns that reference person(full_name).

Migration 0023 added integer FK columns and backfilled them, but left the old
text columns in place because batch_alter_table on collecting_event was blocked
by collection_object's FK constraint.  Now we drop them with FK enforcement
suspended so the batch recreate can proceed.

Dropped columns:
  collecting_event."dwc:recordedBy"
  taxon_determination."dwc:identifiedBy"
  person_defaults.default_identified_by
  person_defaults.default_recorded_by

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-09
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("collecting_event", recreate="always") as batch_op:
            batch_op.drop_column("dwc:recordedBy")

        with op.batch_alter_table("taxon_determination", recreate="always") as batch_op:
            batch_op.drop_column("dwc:identifiedBy")

        with op.batch_alter_table("person_defaults", recreate="always") as batch_op:
            batch_op.drop_column("default_identified_by")
            batch_op.drop_column("default_recorded_by")
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    raise NotImplementedError("Downgrade from 0024 not implemented")
