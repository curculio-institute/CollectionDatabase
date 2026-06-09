"""Add person_defaults table — stores default identifiedBy/recordedBy as proper FKs
to person(full_name).  The table always has exactly one row.

Upgrade seeds the row from config.json if the referenced names already exist in
the person table; otherwise seeds NULL.  The two fields are removed from
AppConfig / config.json after this migration.

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-09
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    try:
        op.create_table(
            "person_defaults",
            sa.Column(
                "default_identified_by",
                sa.String,
                sa.ForeignKey("person.full_name", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column(
                "default_recorded_by",
                sa.String,
                sa.ForeignKey("person.full_name", ondelete="RESTRICT"),
                nullable=True,
            ),
        )

        # Try to seed from config.json; only keep names that exist in person.
        idby: str | None = None
        recby: str | None = None
        config_path = Path(__file__).parent.parent.parent / "data" / "config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                idby = data.get("default_identified_by") or None
                recby = data.get("default_recorded_by") or None
            except Exception:
                pass

        for name in (idby, recby):
            if name:
                row = conn.execute(
                    sa.text("SELECT full_name FROM person WHERE full_name = :n"),
                    {"n": name},
                ).fetchone()
                if row is None:
                    if name == idby:
                        idby = None
                    else:
                        recby = None

        conn.execute(
            sa.text(
                "INSERT INTO person_defaults (default_identified_by, default_recorded_by)"
                " VALUES (:ib, :rb)"
            ),
            {"ib": idby, "rb": recby},
        )
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    op.drop_table("person_defaults")
