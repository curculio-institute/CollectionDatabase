"""biological_association — object arc gains field_occurrence

The object of an association can now be a field_occurrence (a host / associated organism
recorded as its own HumanObservation), so the object exclusive arc widens from
(collection_object XOR taxon) to **exactly one of** (collection_object, taxon,
field_occurrence). object_taxon stays for the lightweight "collected on <taxon>, no
observation record" case. The subject arc is unchanged. See docs/field_occurrence.md.

SQLite cannot ALTER … ADD CONSTRAINT, so biological_association is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints"), the new DDL
re-declares STRICT, the subject arc CHECK verbatim, and every FK ON DELETE action, adds
the new field_occurrence FK (ON DELETE RESTRICT, like the other object FKs) and the
widened object arc CHECK. These exclusive-arc CHECKs were historically unnamed (mig 0007)
and re-dropped once already (CLAUDE.md §8) — they are named here. tests/test_schema_integrity.py
guards the result.

Downgrade assumes no field_occurrence-object associations exist (the arc narrows back to
two); such a row would fail it loudly rather than being silently dropped.

Revision ID: 0061
Revises: 0060
"""
from typing import Union

from alembic import op

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels = None
depends_on = None

_SUBJECT_ARC = (
    'CONSTRAINT ck_ba_subject_exclusive_arc CHECK ('
    '(subject_collection_object_id IS NOT NULL AND subject_taxon_id IS NULL) OR '
    '(subject_collection_object_id IS NULL AND subject_taxon_id IS NOT NULL))'
)
_OBJECT_ARC_3 = (
    'CONSTRAINT ck_ba_object_exclusive_arc CHECK ('
    '((object_collection_object_id IS NOT NULL) + (object_taxon_id IS NOT NULL) + '
    '(object_field_occurrence_id IS NOT NULL)) = 1)'
)
_OBJECT_ARC_2 = (
    'CONSTRAINT ck_ba_object_exclusive_arc CHECK ('
    '(object_collection_object_id IS NOT NULL AND object_taxon_id IS NULL) OR '
    '(object_collection_object_id IS NULL AND object_taxon_id IS NOT NULL))'
)

_COLS = ('id, biological_relationship_id, subject_collection_object_id, subject_taxon_id, '
         'object_collection_object_id, object_taxon_id, notes, created_at, updated_at')


def _new_table(name: str, *, with_fo: bool) -> str:
    fo_col = "    object_field_occurrence_id INTEGER,\n" if with_fo else ""
    fo_fk = ("\n    FOREIGN KEY(object_field_occurrence_id) REFERENCES field_occurrence (id) "
             "ON DELETE RESTRICT,") if with_fo else ""
    object_arc = _OBJECT_ARC_3 if with_fo else _OBJECT_ARC_2
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    biological_relationship_id INTEGER NOT NULL,
    subject_collection_object_id INTEGER,
    subject_taxon_id INTEGER,
    object_collection_object_id INTEGER,
    object_taxon_id INTEGER,
{fo_col}    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    {_SUBJECT_ARC},
    {object_arc},
    FOREIGN KEY(biological_relationship_id) REFERENCES biological_relationship (id) ON DELETE RESTRICT,
    FOREIGN KEY(subject_collection_object_id) REFERENCES collection_object (id) ON DELETE RESTRICT,
    FOREIGN KEY(subject_taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT,
    FOREIGN KEY(object_collection_object_id) REFERENCES collection_object (id) ON DELETE RESTRICT,{fo_fk}
    FOREIGN KEY(object_taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT
) STRICT'''


def _rebuild(*, with_fo: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("biological_association_new", with_fo=with_fo))
        op.execute(f"INSERT INTO biological_association_new ({_COLS}) "
                   f"SELECT {_COLS} FROM biological_association")
        op.execute("DROP TABLE biological_association")
        op.execute("ALTER TABLE biological_association_new RENAME TO biological_association")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_fo=True)


def downgrade() -> None:
    _rebuild(with_fo=False)
