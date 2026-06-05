"""Simplify biological_relationship: keep id + name, add taxonworksID

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-04

Dropped: inverted_name, definition, is_transitive, is_reflexive
Added:   taxonworksID — TaxonWorks internal ID for the relationship type,
         used to match local relationships to TW records when creating
         biological associations via the TW API.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS biological_relationship_new")
    op.execute("""
        CREATE TABLE biological_relationship_new (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            taxonworksID    INTEGER,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        ) STRICT
    """)
    op.execute("""
        INSERT INTO biological_relationship_new (id, name, created_at, updated_at)
        SELECT id, name, created_at, updated_at
        FROM biological_relationship
    """)
    op.execute("DROP TABLE biological_relationship")
    op.execute("ALTER TABLE biological_relationship_new RENAME TO biological_relationship")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS biological_relationship_old")
    op.execute("""
        CREATE TABLE biological_relationship_old (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            inverted_name   TEXT,
            definition      TEXT,
            is_transitive   INTEGER NOT NULL DEFAULT 0 CHECK (is_transitive IN (0, 1)),
            is_reflexive    INTEGER NOT NULL DEFAULT 0 CHECK (is_reflexive IN (0, 1)),
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        ) STRICT
    """)
    op.execute("""
        INSERT INTO biological_relationship_old (id, name, created_at, updated_at)
        SELECT id, name, created_at, updated_at
        FROM biological_relationship
    """)
    op.execute("DROP TABLE biological_relationship")
    op.execute("ALTER TABLE biological_relationship_old RENAME TO biological_relationship")
