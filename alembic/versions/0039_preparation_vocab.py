"""preparation — controlled vocabulary for collection_object.preparations

`preparations` was a free-text DwC column. It becomes a single-name controlled
vocabulary (like person), so it can be edited / merged: a `preparation(id, name)`
table + a `collection_object.preparation_id` FK (ON DELETE RESTRICT, mirroring the
person FKs). Existing distinct text values are migrated into rows and linked, then
the `dwc:preparations` text column is dropped. The DwC `preparations` string is
resolved from preparation.name at export time (mirrors recordedBy / identifiedBy).

Native `ALTER TABLE … ADD COLUMN` (with the FK) and `DROP COLUMN` are used (SQLite
≥ 3.35) so the big STRICT collection_object table is NOT rebuilt — every other
STRICT/CHECK/UNIQUE/FK constraint is preserved untouched (CLAUDE.md migration
discipline; guarded by tests/test_schema_integrity.py).

Revision ID: 0039
Revises: 0038
"""
from typing import Union

from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE preparation (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        ) STRICT
    """)
    # FK column added natively (no table rebuild → all existing constraints kept).
    op.execute(
        'ALTER TABLE collection_object ADD COLUMN preparation_id INTEGER '
        'REFERENCES preparation(id) ON DELETE RESTRICT'
    )
    # Migrate existing distinct, non-empty preparation strings into rows…
    op.execute("""
        INSERT INTO preparation (name, created_at, updated_at)
        SELECT DISTINCT TRIM("dwc:preparations"),
               strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               strftime('%Y-%m-%dT%H:%M:%fZ','now')
          FROM collection_object
         WHERE "dwc:preparations" IS NOT NULL AND TRIM("dwc:preparations") != ''
    """)
    # …and link each specimen to its preparation row.
    op.execute("""
        UPDATE collection_object
           SET preparation_id = (
               SELECT p.id FROM preparation p
                WHERE p.name = TRIM(collection_object."dwc:preparations")
           )
         WHERE "dwc:preparations" IS NOT NULL AND TRIM("dwc:preparations") != ''
    """)
    op.execute('ALTER TABLE collection_object DROP COLUMN "dwc:preparations"')


def downgrade() -> None:
    op.execute('ALTER TABLE collection_object ADD COLUMN "dwc:preparations" TEXT')
    op.execute("""
        UPDATE collection_object
           SET "dwc:preparations" = (
               SELECT p.name FROM preparation p WHERE p.id = collection_object.preparation_id
           )
         WHERE preparation_id IS NOT NULL
    """)
    op.execute('ALTER TABLE collection_object DROP COLUMN preparation_id')
    op.drop_table("preparation")
