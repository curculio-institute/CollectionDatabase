"""media + media_attachment — file attachments for specimens, events, associations (#48)

Two STRICT tables:

* ``media`` — one row per distinct stored file (the bytes live content-addressed on disk;
  see app/services/media.py). ``sha256`` is UNIQUE (de-dup). ``category`` is the filter
  key, CHECK-constrained to the Audubon-Core-style set + "Sequence" (genetic data, FASTA).
* ``media_attachment`` — links a media row to exactly one of a collection_object, a
  collecting_event, or a biological_association (exclusive-arc CHECK), with per-attachment
  caption / is_primary / sort_order. All target FKs ON DELETE CASCADE; media_id CASCADE.

Raw ``CREATE TABLE … STRICT`` DDL (not autogen) so STRICT typing and every CHECK / UNIQUE
/ FK action are declared explicitly and survive (CLAUDE.md migration discipline; guarded
by tests/test_schema_integrity.py).

Revision ID: 0035
Revises: 0034
"""
from typing import Union

from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE media (
            id                INTEGER PRIMARY KEY,
            sha256            TEXT    NOT NULL UNIQUE,
            relative_path     TEXT    NOT NULL,
            category          TEXT    NOT NULL
                                  CHECK (category IN ('Image','Sound','Video','Document','Sequence','Other')),
            format            TEXT,
            original_filename TEXT,
            byte_size         INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
            width             INTEGER,
            height            INTEGER,
            title             TEXT,
            creator           TEXT,
            capture_date      TEXT,
            license           TEXT,
            rights_holder     TEXT,
            source            TEXT,
            remarks           TEXT,
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL
        ) STRICT
    """)

    op.execute("""
        CREATE TABLE media_attachment (
            id                        INTEGER PRIMARY KEY,
            media_id                  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            collection_object_id      INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            collecting_event_id       INTEGER REFERENCES collecting_event(id) ON DELETE CASCADE,
            biological_association_id INTEGER REFERENCES biological_association(id) ON DELETE CASCADE,
            caption                   TEXT,
            is_primary                INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
            sort_order                INTEGER NOT NULL DEFAULT 0,
            created_at                TEXT    NOT NULL,
            updated_at                TEXT    NOT NULL,
            CONSTRAINT ck_media_attachment_exclusive_arc CHECK (
                (collection_object_id IS NOT NULL AND collecting_event_id IS NULL AND biological_association_id IS NULL) OR
                (collection_object_id IS NULL AND collecting_event_id IS NOT NULL AND biological_association_id IS NULL) OR
                (collection_object_id IS NULL AND collecting_event_id IS NULL AND biological_association_id IS NOT NULL)
            )
        ) STRICT
    """)


def downgrade() -> None:
    op.drop_table("media_attachment")
    op.drop_table("media")
