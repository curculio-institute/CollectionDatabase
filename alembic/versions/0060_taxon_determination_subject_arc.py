"""taxon_determination — subject exclusive arc (collection_object XOR field_occurrence)

A determination can now describe either a held specimen or a field_occurrence
(HumanObservation), so its subject becomes an exclusive arc: exactly one of
``collection_object_id`` / ``field_occurrence_id`` is set. This lets a field occurrence
reuse the whole determination machinery — including the open-nomenclature qualifier —
instead of a second table. See docs/field_occurrence.md.

SQLite cannot ALTER … ADD CONSTRAINT, so taxon_determination is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints"), the new DDL
re-declares STRICT, both existing CHECKs (is_current, identificationQualifier), the
is_current server default, and every FK ON DELETE action verbatim, and adds the new
field_occurrence FK (ON DELETE CASCADE, mirroring the collection_object one) + the arc
CHECK. The "one current determination" partial-unique index is duplicated per subject
(…_per_co and …_per_fo); SQLite's distinct-NULL semantics keep each index scoped to its
own subject. tests/test_schema_integrity.py guards the result.

Downgrade assumes no field_occurrence determinations exist yet (collection_object_id
becomes NOT NULL again); a determination on a field occurrence would fail it loudly.

Revision ID: 0060
Revises: 0059
"""
from typing import Union

from alembic import op

from app.vocab import IDENTIFICATION_QUALIFIERS

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels = None
depends_on = None

_QUAL_LIST = ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS)
_QUAL_CHECK = (
    'CONSTRAINT ck_td_identification_qualifier CHECK ('
    '"dwc:identificationQualifier" IS NULL '
    f'OR "dwc:identificationQualifier" IN ({_QUAL_LIST}))'
)
_ARC_CHECK = (
    'CONSTRAINT ck_td_subject_exclusive_arc CHECK ('
    '(collection_object_id IS NOT NULL AND field_occurrence_id IS NULL) OR '
    '(collection_object_id IS NULL AND field_occurrence_id IS NOT NULL))'
)

# Columns carried across the rebuild (present in BOTH schemas — field_occurrence_id is
# new, so it is not selected; it defaults to NULL on upgrade).
_COLS = ('id, collection_object_id, taxon_id, "dwc:verbatimIdentification", "dwc:sex", '
         '"dwc:typeStatus", identified_by_id, "dwc:dateIdentified", '
         '"dwc:identificationQualifier", "dwc:identificationRemarks", is_current, '
         'created_at, updated_at')


def _new_table(name: str, *, with_arc: bool) -> str:
    if with_arc:
        subject = ("    collection_object_id INTEGER,\n"
                   "    field_occurrence_id INTEGER,\n")
        arc = f",\n    {_ARC_CHECK}"
        fo_fk = ("\n    FOREIGN KEY(field_occurrence_id) REFERENCES field_occurrence (id) "
                 "ON DELETE CASCADE,")
    else:
        subject = "    collection_object_id INTEGER NOT NULL,\n"
        arc = ""
        fo_fk = ""
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
{subject}    taxon_id INTEGER NOT NULL,
    "dwc:verbatimIdentification" TEXT,
    "dwc:sex" TEXT,
    "dwc:typeStatus" TEXT,
    identified_by_id INTEGER,
    "dwc:dateIdentified" TEXT,
    "dwc:identificationQualifier" TEXT,
    "dwc:identificationRemarks" TEXT,
    is_current INTEGER DEFAULT 1 NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_td_is_current_bool CHECK (is_current IN (0, 1)),
    {_QUAL_CHECK}{arc},
    FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE,{fo_fk}
    FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT,
    FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT
) STRICT'''


def _indexes(table: str, *, with_arc: bool) -> list[str]:
    idx = [
        f"CREATE INDEX ix_td_co_id ON {table} (collection_object_id)",
        f"CREATE INDEX ix_td_taxon_id ON {table} (taxon_id)",
        f"CREATE UNIQUE INDEX uq_td_one_current_per_co ON {table} "
        "(collection_object_id) WHERE is_current = 1",
    ]
    if with_arc:
        idx += [
            f"CREATE INDEX ix_td_fo_id ON {table} (field_occurrence_id)",
            f"CREATE UNIQUE INDEX uq_td_one_current_per_fo ON {table} "
            "(field_occurrence_id) WHERE is_current = 1",
        ]
    return idx


def _rebuild(*, with_arc: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("taxon_determination_new", with_arc=with_arc))
        op.execute(f"INSERT INTO taxon_determination_new ({_COLS}) "
                   f"SELECT {_COLS} FROM taxon_determination")
        op.execute("DROP TABLE taxon_determination")
        op.execute("ALTER TABLE taxon_determination_new RENAME TO taxon_determination")
        for stmt in _indexes("taxon_determination", with_arc=with_arc):
            op.execute(stmt)
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_arc=True)


def downgrade() -> None:
    _rebuild(with_arc=False)
