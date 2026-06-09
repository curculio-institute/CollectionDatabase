"""Change person FK columns from text (full_name) to integer (person.id).

Affected tables:
  collecting_event:    adds recorded_by_id INTEGER FK → person.id
  taxon_determination: adds identified_by_id INTEGER FK → person.id
  person_defaults:     adds default_identified_by_id / default_recorded_by_id INTEGER FKs

The old text FK columns (dwc:recordedBy, dwc:identifiedBy, default_identified_by,
default_recorded_by) are NOT dropped in this migration.  Dropping them would require
PRAGMA foreign_keys = OFF, which is a no-op inside a transaction in SQLite; the
batch_alter_table approach fails because DROP TABLE collecting_event is blocked by
collection_object's FK with enforcement ON.  The old columns are orphaned dead weight
(no application code reads or writes them) but cause no functional harm.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-09
"""
from __future__ import annotations
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    def _col_exists(table: str, col: str) -> bool:
        rows = conn.execute(sa.text(f"PRAGMA table_info(\"{table}\")")).fetchall()
        return any(r[1] == col for r in rows)

    # ── 1. Add new integer FK columns (idempotent) ─────────────────────────────
    if not _col_exists("collecting_event", "recorded_by_id"):
        conn.execute(sa.text(
            "ALTER TABLE collecting_event "
            "ADD COLUMN recorded_by_id INTEGER REFERENCES person(id) ON DELETE RESTRICT"
        ))
    if not _col_exists("taxon_determination", "identified_by_id"):
        conn.execute(sa.text(
            "ALTER TABLE taxon_determination "
            "ADD COLUMN identified_by_id INTEGER REFERENCES person(id) ON DELETE RESTRICT"
        ))
    if not _col_exists("person_defaults", "default_identified_by_id"):
        conn.execute(sa.text(
            "ALTER TABLE person_defaults "
            "ADD COLUMN default_identified_by_id INTEGER REFERENCES person(id) ON DELETE RESTRICT"
        ))
    if not _col_exists("person_defaults", "default_recorded_by_id"):
        conn.execute(sa.text(
            "ALTER TABLE person_defaults "
            "ADD COLUMN default_recorded_by_id INTEGER REFERENCES person(id) ON DELETE RESTRICT"
        ))

    # ── 2. Backfill: look up person.id by matching full_name ──────────────────
    if _col_exists("collecting_event", "dwc:recordedBy"):
        conn.execute(sa.text(
            "UPDATE collecting_event "
            "SET recorded_by_id = ("
            "  SELECT p.id FROM person p WHERE p.full_name = collecting_event.\"dwc:recordedBy\""
            ") "
            "WHERE collecting_event.\"dwc:recordedBy\" IS NOT NULL"
        ))
    if _col_exists("taxon_determination", "dwc:identifiedBy"):
        conn.execute(sa.text(
            "UPDATE taxon_determination "
            "SET identified_by_id = ("
            "  SELECT p.id FROM person p WHERE p.full_name = taxon_determination.\"dwc:identifiedBy\""
            ") "
            "WHERE taxon_determination.\"dwc:identifiedBy\" IS NOT NULL"
        ))
    if _col_exists("person_defaults", "default_identified_by"):
        conn.execute(sa.text(
            "UPDATE person_defaults "
            "SET default_identified_by_id = ("
            "  SELECT p.id FROM person p WHERE p.full_name = person_defaults.default_identified_by"
            ") "
            "WHERE person_defaults.default_identified_by IS NOT NULL"
        ))
    if _col_exists("person_defaults", "default_recorded_by"):
        conn.execute(sa.text(
            "UPDATE person_defaults "
            "SET default_recorded_by_id = ("
            "  SELECT p.id FROM person p WHERE p.full_name = person_defaults.default_recorded_by"
            ") "
            "WHERE person_defaults.default_recorded_by IS NOT NULL"
        ))


def downgrade() -> None:
    raise NotImplementedError("Downgrade from 0023 not implemented")
