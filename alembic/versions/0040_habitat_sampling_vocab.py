"""habitat + sampling_protocol — controlled vocabularies for collecting_event

`dwc:habitat` (free text) and `dwc:samplingProtocol` (a hardcoded UI list) become
single-name controlled vocabularies like `preparation`, so they can be edited /
merged: `habitat(id, name)` + `sampling_protocol(id, name)` tables and
`collecting_event.habitat_id` / `.sampling_protocol_id` FKs (ON DELETE RESTRICT).
The sampling_protocol table is seeded with the curated starting set (was
app/vocab.py::SAMPLING_PROTOCOLS). Existing distinct text values are migrated into
rows and linked, then the two text columns are dropped. DwC strings resolve from
name at export.

Native ADD COLUMN (with FK) + DROP COLUMN (SQLite ≥ 3.35), so the STRICT
collecting_event table is NOT rebuilt — every other constraint is preserved
(CLAUDE.md migration discipline).

Revision ID: 0040
Revises: 0039
"""
from typing import Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels = None
depends_on = None

# Curated starting set for sampling_protocol (was app/vocab.py SAMPLING_PROTOCOLS).
_SAMPLING_SEED = [
    "hand collecting", "sweep net", "beating", "pitfall trap",
    "light trap", "sifting", "bark peeling", "rearing", "Berlese funnel",
    "yellow pan trap", "window trap", "observation",
]


def _create_vocab_table(name: str) -> None:
    op.execute(f"""
        CREATE TABLE {name} (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        ) STRICT
    """)


def _migrate_column(*, vocab_table: str, fk_col: str, text_col: str) -> None:
    """Add the FK column, fold distinct existing text values into rows + link,
    then drop the old text column."""
    op.execute(
        f'ALTER TABLE collecting_event ADD COLUMN {fk_col} INTEGER '
        f'REFERENCES {vocab_table}(id) ON DELETE RESTRICT'
    )
    op.execute(f"""
        INSERT OR IGNORE INTO {vocab_table} (name, created_at, updated_at)
        SELECT DISTINCT TRIM("{text_col}"),
               strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               strftime('%Y-%m-%dT%H:%M:%fZ','now')
          FROM collecting_event
         WHERE "{text_col}" IS NOT NULL AND TRIM("{text_col}") != ''
    """)
    op.execute(f"""
        UPDATE collecting_event
           SET {fk_col} = (SELECT v.id FROM {vocab_table} v WHERE v.name = TRIM(collecting_event."{text_col}"))
         WHERE "{text_col}" IS NOT NULL AND TRIM("{text_col}") != ''
    """)
    op.execute(f'ALTER TABLE collecting_event DROP COLUMN "{text_col}"')


def upgrade() -> None:
    _create_vocab_table("habitat")
    _create_vocab_table("sampling_protocol")

    # Seed sampling_protocol with the curated list before migrating real values.
    values = ",".join(
        f"('{nm.replace(chr(39), chr(39) * 2)}', "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        for nm in _SAMPLING_SEED
    )
    op.execute(
        f"INSERT OR IGNORE INTO sampling_protocol (name, created_at, updated_at) VALUES {values}"
    )

    _migrate_column(vocab_table="habitat", fk_col="habitat_id", text_col="dwc:habitat")
    _migrate_column(vocab_table="sampling_protocol", fk_col="sampling_protocol_id",
                    text_col="dwc:samplingProtocol")


def downgrade() -> None:
    op.execute('ALTER TABLE collecting_event ADD COLUMN "dwc:habitat" TEXT')
    op.execute("""
        UPDATE collecting_event
           SET "dwc:habitat" = (SELECT h.name FROM habitat h WHERE h.id = collecting_event.habitat_id)
         WHERE habitat_id IS NOT NULL
    """)
    op.execute('ALTER TABLE collecting_event DROP COLUMN habitat_id')

    op.execute('ALTER TABLE collecting_event ADD COLUMN "dwc:samplingProtocol" TEXT')
    op.execute("""
        UPDATE collecting_event
           SET "dwc:samplingProtocol" = (SELECT s.name FROM sampling_protocol s WHERE s.id = collecting_event.sampling_protocol_id)
         WHERE sampling_protocol_id IS NOT NULL
    """)
    op.execute('ALTER TABLE collecting_event DROP COLUMN sampling_protocol_id')

    op.drop_table("sampling_protocol")
    op.drop_table("habitat")
