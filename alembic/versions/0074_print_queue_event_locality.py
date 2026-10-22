"""print_queue — locality labels for a collecting event (no specimen)

Until now every queued ``data`` (locality) row had to point at a specimen, because the
label is derived from that specimen's collecting event. The Labels tab and the Records
event sheet now queue locality labels for an **event** directly — e.g. labels printed in
the field or before the specimens are mounted.

* ``collecting_event_id``  FK → collecting_event, ON DELETE CASCADE (like the other queue
                           FKs: a queued label for a deleted event is simply dropped)

The exclusive arc's ``data`` arm becomes: a specimen XOR an event. Every other row keeps
``collecting_event_id`` NULL.

SQLite cannot alter a CHECK, so print_queue is REBUILT. Per the DB-1 discipline the new
DDL re-declares STRICT, ``ck_print_queue_label_type``, the arc, the plain-label CHECKs
(``ck_print_queue_plain_fields``, ``…_identification_qualifier``,
``…_date_identified_no_interval``) and every FK ON DELETE action. print_queue has no
indexes, triggers or server defaults. tests/test_schema_integrity.py guards the result.

Downgrade deletes any queued event locality labels first — the old arc cannot represent
them. They are pending print jobs, not collection data.

Revision ID: 0074
Revises: 0073
"""
from typing import Union

from alembic import op

from app.vocab import IDENTIFICATION_QUALIFIERS

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels = None
depends_on = None

_QUAL_LIST = ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS)

_OLD_COLS = ("id, label_type, print_group_id, source, text_override, "
             "collection_object_id, label_code_id, taxon_determination_id, "
             'taxon_id, identified_by_id, "dwc:dateIdentified", "dwc:typeStatus", '
             '"dwc:identificationQualifier", "dwc:sex", created_at, updated_at')

# 0071's arc, unchanged (the downgrade target).
_OLD_ARC = (
    "CONSTRAINT ck_print_queue_exclusive_arc CHECK ("
    "(label_type = 'data' AND collection_object_id IS NOT NULL"
    " AND label_code_id IS NULL AND taxon_id IS NULL)"
    " OR (label_type = 'determination' AND label_code_id IS NULL"
    " AND ((collection_object_id IS NOT NULL AND taxon_id IS NULL)"
    " OR (collection_object_id IS NULL AND taxon_id IS NOT NULL)))"
    " OR (label_type = 'identifier' AND label_code_id IS NOT NULL"
    " AND collection_object_id IS NULL AND taxon_id IS NULL))"
)

_NEW_ARC = (
    "CONSTRAINT ck_print_queue_exclusive_arc CHECK ("
    "(label_type = 'data' AND label_code_id IS NULL AND taxon_id IS NULL"
    " AND ((collection_object_id IS NOT NULL AND collecting_event_id IS NULL)"
    " OR (collection_object_id IS NULL AND collecting_event_id IS NOT NULL)))"
    " OR (label_type = 'determination' AND label_code_id IS NULL"
    " AND collecting_event_id IS NULL"
    " AND ((collection_object_id IS NOT NULL AND taxon_id IS NULL)"
    " OR (collection_object_id IS NULL AND taxon_id IS NOT NULL)))"
    " OR (label_type = 'identifier' AND label_code_id IS NOT NULL"
    " AND collection_object_id IS NULL AND taxon_id IS NULL"
    " AND collecting_event_id IS NULL))"
)


def _new_table(name: str, *, event: bool) -> str:
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    label_type TEXT NOT NULL,
    print_group_id INTEGER,
    source TEXT,
    text_override TEXT,
    collection_object_id INTEGER,
    label_code_id INTEGER,
    taxon_determination_id INTEGER,
    taxon_id INTEGER,
    identified_by_id INTEGER,
    "dwc:dateIdentified" TEXT,
    "dwc:typeStatus" TEXT,
    "dwc:identificationQualifier" TEXT,
    "dwc:sex" TEXT,{"""
    collecting_event_id INTEGER,""" if event else ""}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_print_queue_label_type CHECK (label_type IN ('data', 'determination', 'identifier')),
    {_NEW_ARC if event else _OLD_ARC},
    CONSTRAINT ck_print_queue_plain_fields CHECK (
        (taxon_id IS NOT NULL AND taxon_determination_id IS NULL)
        OR (taxon_id IS NULL AND identified_by_id IS NULL
            AND "dwc:dateIdentified" IS NULL AND "dwc:typeStatus" IS NULL
            AND "dwc:identificationQualifier" IS NULL AND "dwc:sex" IS NULL)),
    CONSTRAINT ck_print_queue_identification_qualifier CHECK (
        "dwc:identificationQualifier" IS NULL
        OR "dwc:identificationQualifier" IN ({_QUAL_LIST})),
    CONSTRAINT ck_print_queue_date_identified_no_interval CHECK (
        "dwc:dateIdentified" IS NULL OR "dwc:dateIdentified" NOT LIKE '%/%'),
    FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE,
    FOREIGN KEY(label_code_id) REFERENCES label_code (id) ON DELETE CASCADE,
    FOREIGN KEY(taxon_determination_id) REFERENCES taxon_determination (id) ON DELETE CASCADE,
    FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE CASCADE,
    FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT{""",
    FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE CASCADE""" if event else ""}
) STRICT'''


def _rebuild(*, event: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("print_queue_new", event=event))
        op.execute(f"INSERT INTO print_queue_new ({_OLD_COLS}) "
                   f"SELECT {_OLD_COLS} FROM print_queue")
        op.execute("DROP TABLE print_queue")
        op.execute("ALTER TABLE print_queue_new RENAME TO print_queue")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(event=True)


def downgrade() -> None:
    op.execute("DELETE FROM print_queue WHERE collecting_event_id IS NOT NULL")
    _rebuild(event=False)
