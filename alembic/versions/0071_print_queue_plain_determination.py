"""print_queue — plain identification labels (a determination label with no specimen)

Until now every queued ``determination`` row had to point at a specimen
(``ck_print_queue_exclusive_arc``), because the label is derived from that specimen's
determination. The Labels tab now queues *plain* identification labels — N copies of a
chosen name + determiner + date + type status + qualifier + sex, to pin by hand —
exactly as it already queues identifier labels that belong to no specimen yet.

Nothing else exists to derive such a label from, so the queue row carries the content:

* ``taxon_id``            FK → taxon, ON DELETE CASCADE (like the other queue FKs: a
                          queued label for a deleted name is simply dropped)
* ``identified_by_id``    FK → person, ON DELETE RESTRICT (the project's person rule;
                          ``merge_persons`` re-points it via its PRAGMA FK discovery)
* ``dwc:dateIdentified`` / ``dwc:typeStatus`` / ``dwc:identificationQualifier`` / ``dwc:sex``

The exclusive arc gains a third arm — a determination row has a specimen XOR a taxon —
and ``ck_print_queue_plain_fields`` keeps the content columns NULL on every other row
(and a plain row un-pinned from any ``taxon_determination``). The qualifier and
no-interval-date CHECKs mirror ``taxon_determination`` (0058 / 0069). ``dwc:sex`` has no
CHECK there either (free text by design, see app/vocab.py).

SQLite cannot alter a CHECK, so print_queue is REBUILT. Per the DB-1 discipline the new
DDL re-declares STRICT, ``ck_print_queue_label_type``, the arc, and all three existing
FK ON DELETE CASCADE actions (collection_object, label_code, taxon_determination).
print_queue has no indexes and no server defaults. tests/test_schema_integrity.py
guards the result.

Downgrade deletes any queued plain labels first — the old arc cannot represent them.
They are pending print jobs, not collection data.

Revision ID: 0071
Revises: 0070
"""
from typing import Union

from alembic import op

from app.vocab import IDENTIFICATION_QUALIFIERS

revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels = None
depends_on = None

_QUAL_LIST = ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS)

_OLD_COLS = ("id, label_type, print_group_id, source, text_override, "
             "collection_object_id, label_code_id, taxon_determination_id, "
             "created_at, updated_at")

_OLD_ARC = (
    "CONSTRAINT ck_print_queue_exclusive_arc CHECK ("
    "(label_type IN ('data', 'determination')  AND collection_object_id IS NOT NULL"
    "  AND label_code_id IS NULL) OR (label_type = 'identifier'"
    "  AND label_code_id IS NOT NULL  AND collection_object_id IS NULL))"
)

_NEW_ARC = (
    "CONSTRAINT ck_print_queue_exclusive_arc CHECK ("
    "(label_type = 'data' AND collection_object_id IS NOT NULL"
    " AND label_code_id IS NULL AND taxon_id IS NULL)"
    " OR (label_type = 'determination' AND label_code_id IS NULL"
    " AND ((collection_object_id IS NOT NULL AND taxon_id IS NULL)"
    " OR (collection_object_id IS NULL AND taxon_id IS NOT NULL)))"
    " OR (label_type = 'identifier' AND label_code_id IS NOT NULL"
    " AND collection_object_id IS NULL AND taxon_id IS NULL))"
)

_PLAIN_COLUMNS = '''
    taxon_id INTEGER,
    identified_by_id INTEGER,
    "dwc:dateIdentified" TEXT,
    "dwc:typeStatus" TEXT,
    "dwc:identificationQualifier" TEXT,
    "dwc:sex" TEXT,'''

_PLAIN_CONSTRAINTS = f'''
    CONSTRAINT ck_print_queue_plain_fields CHECK (
        (taxon_id IS NOT NULL AND taxon_determination_id IS NULL)
        OR (taxon_id IS NULL AND identified_by_id IS NULL
            AND "dwc:dateIdentified" IS NULL AND "dwc:typeStatus" IS NULL
            AND "dwc:identificationQualifier" IS NULL AND "dwc:sex" IS NULL)),
    CONSTRAINT ck_print_queue_identification_qualifier CHECK (
        "dwc:identificationQualifier" IS NULL
        OR "dwc:identificationQualifier" IN ({_QUAL_LIST})),
    CONSTRAINT ck_print_queue_date_identified_no_interval CHECK (
        "dwc:dateIdentified" IS NULL OR "dwc:dateIdentified" NOT LIKE '%/%'),'''

_PLAIN_FKS = ''',
    FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE CASCADE,
    FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT'''


def _new_table(name: str, *, plain: bool) -> str:
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    label_type TEXT NOT NULL,
    print_group_id INTEGER,
    source TEXT,
    text_override TEXT,
    collection_object_id INTEGER,
    label_code_id INTEGER,
    taxon_determination_id INTEGER,{_PLAIN_COLUMNS if plain else ""}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_print_queue_label_type CHECK (label_type IN ('data', 'determination', 'identifier')),
    {_NEW_ARC if plain else _OLD_ARC},{_PLAIN_CONSTRAINTS if plain else ""}
    FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE,
    FOREIGN KEY(label_code_id) REFERENCES label_code (id) ON DELETE CASCADE,
    FOREIGN KEY(taxon_determination_id) REFERENCES taxon_determination (id) ON DELETE CASCADE{_PLAIN_FKS if plain else ""}
) STRICT'''


def _rebuild(*, plain: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("print_queue_new", plain=plain))
        op.execute(f"INSERT INTO print_queue_new ({_OLD_COLS}) "
                   f"SELECT {_OLD_COLS} FROM print_queue")
        op.execute("DROP TABLE print_queue")
        op.execute("ALTER TABLE print_queue_new RENAME TO print_queue")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(plain=True)


def downgrade() -> None:
    op.execute("DELETE FROM print_queue WHERE taxon_id IS NOT NULL")
    _rebuild(plain=False)
