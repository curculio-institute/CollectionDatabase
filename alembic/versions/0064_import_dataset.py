"""import_dataset + import_dataset_record — staged wholesale import (#39)

The bulk-import workflow (modelled on TaxonWorks' Import Dataset) needs the whole
uploaded file to be a durable, resumable entity, not an in-memory parse: a per-row
record with a status, so an import can be inspected before it writes, resumed after a
restart, and de-duplicated against what is already imported.

Two STRICT tables:
  import_dataset         — one uploaded checklist; kind/status CHECK-constrained, a
                           dataset-level nomenclatural_code default, a resume cursor.
  import_dataset_record  — one source row; raw row as JSON TEXT, per-row status CHECK,
                           the matched taxon (FK ON DELETE SET NULL — deleting a taxon
                           must not delete the audit row, only forget the link).

Both are created fresh (no rebuild), so STRICT + every CHECK/UNIQUE/FK is declared here
directly (DB-1 discipline). Downgrade drops both; the records cascade first.

Revision ID: 0064
Revises: 0063
"""
from typing import Union

from alembic import op

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE import_dataset (
            id                 INTEGER PRIMARY KEY,
            kind               TEXT    NOT NULL,
            name               TEXT    NOT NULL,
            source_filename    TEXT,
            nomenclatural_code TEXT,
            status             TEXT    NOT NULL DEFAULT 'staged',
            import_cursor      INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT    NOT NULL,
            updated_at         TEXT    NOT NULL,
            CONSTRAINT uq_import_dataset_name   UNIQUE (name),
            CONSTRAINT ck_import_dataset_kind   CHECK (kind IN ('taxon')),
            CONSTRAINT ck_import_dataset_status CHECK (status IN ('staged', 'importing', 'completed'))
        ) STRICT
    """)
    op.execute("""
        CREATE TABLE import_dataset_record (
            id                INTEGER PRIMARY KEY,
            import_dataset_id INTEGER NOT NULL REFERENCES import_dataset(id) ON DELETE CASCADE,
            row_index         INTEGER NOT NULL,
            status            TEXT    NOT NULL,
            data              TEXT    NOT NULL,
            resolved_name     TEXT,
            error_message     TEXT,
            taxon_id          INTEGER REFERENCES taxon(id) ON DELETE SET NULL,
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL,
            CONSTRAINT uq_import_dataset_record_row UNIQUE (import_dataset_id, row_index),
            CONSTRAINT ck_import_dataset_record_status
                CHECK (status IN ('ready', 'blocked', 'imported', 'errored'))
        ) STRICT
    """)
    op.execute(
        "CREATE INDEX ix_import_dataset_record_dataset "
        "ON import_dataset_record (import_dataset_id, status)")


def downgrade() -> None:
    op.execute("DROP TABLE import_dataset_record")
    op.execute("DROP TABLE import_dataset")
