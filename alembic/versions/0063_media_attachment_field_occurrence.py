"""media_attachment — arc gains field_occurrence

A HumanObservation (iNat records are photo-first) can carry its own photo, so the
media_attachment exclusive arc widens from three targets to **exactly one of**
(collection_object, collecting_event, biological_association, field_occurrence). See
docs/field_occurrence.md.

SQLite cannot ALTER … ADD CONSTRAINT, so media_attachment is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints"), the new DDL
re-declares STRICT, the is_primary CHECK + its server default, and every FK ON DELETE
action, adds the field_occurrence FK (ON DELETE CASCADE) and the widened arc CHECK.
tests/test_schema_integrity.py guards the result.

Downgrade assumes no field_occurrence attachments exist (arc narrows to three); such a
row would fail it loudly rather than being silently dropped.

Revision ID: 0063
Revises: 0062
"""
from typing import Union

from alembic import op

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels = None
depends_on = None

_COLS = ('id, media_id, collection_object_id, collecting_event_id, '
         'biological_association_id, caption, is_primary, sort_order, '
         'created_at, updated_at')


def _new_table(name: str, *, with_fo: bool) -> str:
    if with_fo:
        fo_col = "            field_occurrence_id       INTEGER REFERENCES field_occurrence(id) ON DELETE CASCADE,\n"
        arc = (
            "                ((collection_object_id IS NOT NULL) + (collecting_event_id IS NOT NULL) + "
            "(biological_association_id IS NOT NULL) + (field_occurrence_id IS NOT NULL)) = 1"
        )
    else:
        fo_col = ""
        arc = (
            "                (collection_object_id IS NOT NULL AND collecting_event_id IS NULL AND biological_association_id IS NULL) OR\n"
            "                (collection_object_id IS NULL AND collecting_event_id IS NOT NULL AND biological_association_id IS NULL) OR\n"
            "                (collection_object_id IS NULL AND collecting_event_id IS NULL AND biological_association_id IS NOT NULL)"
        )
    return f"""CREATE TABLE {name} (
            id                        INTEGER PRIMARY KEY,
            media_id                  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            collection_object_id      INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            collecting_event_id       INTEGER REFERENCES collecting_event(id) ON DELETE CASCADE,
            biological_association_id INTEGER REFERENCES biological_association(id) ON DELETE CASCADE,
{fo_col}            caption                   TEXT,
            is_primary                INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
            sort_order                INTEGER NOT NULL DEFAULT 0,
            created_at                TEXT    NOT NULL,
            updated_at                TEXT    NOT NULL,
            CONSTRAINT ck_media_attachment_exclusive_arc CHECK (
{arc}
            )
        ) STRICT"""


def _rebuild(*, with_fo: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("media_attachment_new", with_fo=with_fo))
        op.execute(f"INSERT INTO media_attachment_new ({_COLS}) "
                   f"SELECT {_COLS} FROM media_attachment")
        op.execute("DROP TABLE media_attachment")
        op.execute("ALTER TABLE media_attachment_new RENAME TO media_attachment")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_fo=True)


def downgrade() -> None:
    _rebuild(with_fo=False)
