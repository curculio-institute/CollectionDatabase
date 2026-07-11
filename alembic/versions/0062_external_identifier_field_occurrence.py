"""external_identifier — arc gains field_occurrence (iNaturalist URL)

A field_occurrence (HumanObservation) very often *originates from* an iNaturalist
observation, so its resolvable URI is a core attribute. The exclusive arc widens from
(collection_object XOR biological_association) to **exactly one of** (collection_object,
biological_association, field_occurrence). See docs/field_occurrence.md.

SQLite cannot ALTER … ADD CONSTRAINT, so external_identifier is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints"), the new DDL
re-declares STRICT and every FK ON DELETE action, adds the field_occurrence FK (ON DELETE
CASCADE, like the others) and the widened arc CHECK. tests/test_schema_integrity.py guards
the result.

Downgrade assumes no field_occurrence identifiers exist (arc narrows to two); such a row
would fail it loudly rather than being silently dropped.

Revision ID: 0062
Revises: 0061
"""
from typing import Union

from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels = None
depends_on = None

_COLS = ('id, collection_object_id, biological_association_id, source, value, label, '
         'remarks, created_at, updated_at')


def _new_table(name: str, *, with_fo: bool) -> str:
    if with_fo:
        fo_col = "            field_occurrence_id       INTEGER REFERENCES field_occurrence(id) ON DELETE CASCADE,\n"
        arc = (
            "                ((collection_object_id IS NOT NULL) + "
            "(biological_association_id IS NOT NULL) + "
            "(field_occurrence_id IS NOT NULL)) = 1"
        )
    else:
        fo_col = ""
        arc = (
            "                (collection_object_id IS NOT NULL AND biological_association_id IS NULL) OR\n"
            "                (collection_object_id IS NULL AND biological_association_id IS NOT NULL)"
        )
    return f"""CREATE TABLE {name} (
            id                        INTEGER PRIMARY KEY,
            collection_object_id      INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            biological_association_id INTEGER REFERENCES biological_association(id) ON DELETE CASCADE,
{fo_col}            source                    TEXT,
            value                     TEXT NOT NULL,
            label                     TEXT,
            remarks                   TEXT,
            created_at                TEXT NOT NULL,
            updated_at                TEXT NOT NULL,
            CONSTRAINT ck_external_identifier_exclusive_arc CHECK (
{arc}
            )
        ) STRICT"""


def _rebuild(*, with_fo: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("external_identifier_new", with_fo=with_fo))
        op.execute(f"INSERT INTO external_identifier_new ({_COLS}) "
                   f"SELECT {_COLS} FROM external_identifier")
        op.execute("DROP TABLE external_identifier")
        op.execute("ALTER TABLE external_identifier_new RENAME TO external_identifier")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_fo=True)


def downgrade() -> None:
    _rebuild(with_fo=False)
