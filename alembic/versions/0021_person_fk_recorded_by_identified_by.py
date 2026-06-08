"""Add FK from collecting_event.recordedBy and taxon_determination.identifiedBy to person.

Backfills any existing free-text names into the person table first, then
recreates both tables with the FK constraint enforced by SQLite.

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-08
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # PRAGMA foreign_keys must be set BEFORE any DML statement; once a DML
    # statement starts a transaction the PRAGMA becomes a no-op in SQLite.
    # collection_object holds a FK to collecting_event, so FK enforcement must
    # be off when batch recreate issues DROP TABLE collecting_event.
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        # 1. Backfill: insert any recorded_by / identified_by values not yet in person.
        #    INSERT OR IGNORE so duplicate names don't raise.
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO person (full_name, created_at, updated_at) "
            "SELECT DISTINCT \"dwc:recordedBy\", datetime('now'), datetime('now') "
            "FROM collecting_event "
            "WHERE \"dwc:recordedBy\" IS NOT NULL AND \"dwc:recordedBy\" != ''"
        ))
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO person (full_name, created_at, updated_at) "
            "SELECT DISTINCT \"dwc:identifiedBy\", datetime('now'), datetime('now') "
            "FROM taxon_determination "
            "WHERE \"dwc:identifiedBy\" IS NOT NULL AND \"dwc:identifiedBy\" != ''"
        ))

        # 2. Recreate collecting_event with FK on dwc:recordedBy → person(full_name).
        with op.batch_alter_table("collecting_event", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_ce_recorded_by_person",
                "person",
                ["dwc:recordedBy"],
                ["full_name"],
            )

        # 3. Recreate taxon_determination with FK on dwc:identifiedBy → person(full_name).
        #    taxon_determination itself holds FKs pointing outward; nothing points to it,
        #    so the recreate would be safe with FK on — but keep enforcement off for the
        #    full block to avoid any cross-table surprises during the INSERT SELECT copy.
        with op.batch_alter_table("taxon_determination", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_td_identified_by_person",
                "person",
                ["dwc:identifiedBy"],
                ["full_name"],
            )
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        with op.batch_alter_table("taxon_determination", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_td_identified_by_person", type_="foreignkey")

        with op.batch_alter_table("collecting_event", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_ce_recorded_by_person", type_="foreignkey")
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
