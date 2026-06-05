"""Make catalogNumber and catalogNamespace NOT NULL on collection_object

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-04

catalogNumber is the specimen's real-world identity: it is on the pin label, survives
the collection moving to a foreign institution, and is the DwC sync key. Every record
must have one before it enters the database.

The partial unique index (uq_co_catalog, WHERE both non-null) is replaced by a plain
UNIQUE constraint in the table definition now that nulls are forbidden.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CREATE = """
    CREATE TABLE collection_object_new (
        id                  INTEGER PRIMARY KEY,
        collecting_event_id INTEGER REFERENCES collecting_event(id) ON DELETE RESTRICT,
        catalogNumber       TEXT NOT NULL,
        catalogNamespace    TEXT NOT NULL,
        basisOfRecord       TEXT NOT NULL DEFAULT 'PreservedSpecimen',
        individualCount     INTEGER NOT NULL DEFAULT 1 CHECK (individualCount >= 0),
        lifeStage           TEXT,
        sex                 TEXT,
        disposition         TEXT,
        ownerInstitutionCode TEXT,
        preparations        TEXT,
        typeStatus          TEXT,
        occurrenceRemarks   TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL,
        UNIQUE (catalogNamespace, catalogNumber)
    ) STRICT
"""

_CREATE_OLD = """
    CREATE TABLE collection_object_old (
        id                  INTEGER PRIMARY KEY,
        collecting_event_id INTEGER REFERENCES collecting_event(id) ON DELETE RESTRICT,
        catalogNumber       TEXT,
        catalogNamespace    TEXT,
        basisOfRecord       TEXT NOT NULL DEFAULT 'PreservedSpecimen',
        individualCount     INTEGER NOT NULL DEFAULT 1 CHECK (individualCount >= 0),
        lifeStage           TEXT,
        sex                 TEXT,
        disposition         TEXT,
        ownerInstitutionCode TEXT,
        preparations        TEXT,
        typeStatus          TEXT,
        occurrenceRemarks   TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    ) STRICT
"""

_COLS = """id, collecting_event_id, catalogNumber, catalogNamespace,
           basisOfRecord, individualCount, lifeStage, sex, disposition,
           ownerInstitutionCode, preparations, typeStatus, occurrenceRemarks,
           created_at, updated_at"""


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS collection_object_new")
    op.execute(_CREATE)
    op.execute(f"INSERT INTO collection_object_new ({_COLS}) SELECT {_COLS} FROM collection_object")
    op.execute("DROP INDEX IF EXISTS uq_co_catalog")
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_new RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS collection_object_old")
    op.execute(_CREATE_OLD)
    op.execute(f"INSERT INTO collection_object_old ({_COLS}) SELECT {_COLS} FROM collection_object")
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_old RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")
