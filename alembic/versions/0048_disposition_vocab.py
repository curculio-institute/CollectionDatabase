"""disposition becomes an editable controlled vocabulary (#76, parent #72)

`collection_object.dwc:disposition` was free TEXT with a closed CHECK (six fixed
values). It becomes a single-name controlled vocabulary (like preparation/habitat),
so the user can record arbitrary holdings — "loaned to Jeffrey", "in the drawer
behind my bed" — and edit/merge them. DwC `disposition` is `[Not mapped]` by the TW
importer, so freeform values never reach TaxonWorks (resolved from disposition.name
at export only).

- new `disposition` table (id, name UNIQUE) STRICT, seeded with the former six values;
- `collection_object.disposition_id` FK → disposition(id) ON DELETE RESTRICT;
- drop `dwc:disposition` TEXT + `ck_co_disposition` CHECK.

Dropping the column is impossible with a native DROP COLUMN while the CHECK references
it, so the STRICT collection_object table is rebuilt by hand (the 0029/0047 recipe),
re-declaring *every* STRICT/CHECK/UNIQUE/FK/DEFAULT per CLAUDE.md migration discipline.
The live DB is empty at this revision, so the data backfill is a no-op.

Revision ID: 0048
Revises: 0047
"""
from typing import Union

from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels = None
depends_on = None

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_SEED = ("in collection", "on loan", "donated", "exchanged", "missing", "destroyed")


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")

    op.execute("""
        CREATE TABLE disposition (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        ) STRICT
    """)
    # Seed the former fixed values …
    for name in _SEED:
        op.execute(
            f"INSERT OR IGNORE INTO disposition (name, created_at, updated_at) "
            f"VALUES ('{name}', {_NOW}, {_NOW})"
        )
    # … plus any other distinct value already in use (no-op on empty DB).
    op.execute(f"""
        INSERT OR IGNORE INTO disposition (name, created_at, updated_at)
        SELECT DISTINCT TRIM("dwc:disposition"), {_NOW}, {_NOW}
          FROM collection_object
         WHERE "dwc:disposition" IS NOT NULL AND TRIM("dwc:disposition") != ''
    """)

    # Rebuild collection_object: disposition_id FK replaces the dwc:disposition text
    # column + its CHECK. Every other STRICT/CHECK/UNIQUE/FK/DEFAULT re-declared.
    op.execute("""
        CREATE TABLE collection_object_new (
            id INTEGER NOT NULL,
            collecting_event_id INTEGER,
            "dwc:catalogNumber" TEXT NOT NULL,
            repository_id INTEGER NOT NULL,
            "dwc:basisOfRecord" TEXT DEFAULT 'PreservedSpecimen' NOT NULL,
            "dwc:individualCount" INTEGER DEFAULT 1 NOT NULL,
            "dwc:lifeStage" TEXT,
            disposition_id INTEGER,
            "dwc:materialEntityRemarks" TEXT,
            preparation_id INTEGER,
            confidential INTEGER NOT NULL DEFAULT 0
                CONSTRAINT ck_co_confidential CHECK (confidential IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_co_repository_catalog UNIQUE (repository_id, "dwc:catalogNumber"),
            CONSTRAINT ck_co_individual_count_non_negative CHECK ("dwc:individualCount" >= 0),
            CONSTRAINT ck_co_basis_of_record CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')),
            FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE RESTRICT,
            FOREIGN KEY(preparation_id) REFERENCES preparation (id) ON DELETE RESTRICT,
            FOREIGN KEY(repository_id) REFERENCES repository (id) ON DELETE RESTRICT,
            FOREIGN KEY(disposition_id) REFERENCES disposition (id) ON DELETE RESTRICT
        ) STRICT
    """)
    op.execute("""
        INSERT INTO collection_object_new
            (id, collecting_event_id, "dwc:catalogNumber", repository_id,
             "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", disposition_id,
             "dwc:materialEntityRemarks", preparation_id, confidential, created_at, updated_at)
        SELECT co.id, co.collecting_event_id, co."dwc:catalogNumber", co.repository_id,
               co."dwc:basisOfRecord", co."dwc:individualCount", co."dwc:lifeStage",
               (SELECT d.id FROM disposition d WHERE d.name = TRIM(co."dwc:disposition")),
               co."dwc:materialEntityRemarks", co.preparation_id, co.confidential, co.created_at, co.updated_at
          FROM collection_object co
    """)
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_new RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")

    bind.exec_driver_sql("PRAGMA foreign_key_check")
    bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    op.execute("""
        CREATE TABLE collection_object_old (
            id INTEGER NOT NULL,
            collecting_event_id INTEGER,
            "dwc:catalogNumber" TEXT NOT NULL,
            repository_id INTEGER NOT NULL,
            "dwc:basisOfRecord" TEXT DEFAULT 'PreservedSpecimen' NOT NULL,
            "dwc:individualCount" INTEGER DEFAULT 1 NOT NULL,
            "dwc:lifeStage" TEXT,
            "dwc:disposition" TEXT,
            "dwc:materialEntityRemarks" TEXT,
            preparation_id INTEGER,
            confidential INTEGER NOT NULL DEFAULT 0
                CONSTRAINT ck_co_confidential CHECK (confidential IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_co_repository_catalog UNIQUE (repository_id, "dwc:catalogNumber"),
            CONSTRAINT ck_co_individual_count_non_negative CHECK ("dwc:individualCount" >= 0),
            CONSTRAINT ck_co_basis_of_record CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')),
            CONSTRAINT ck_co_disposition CHECK ("dwc:disposition" IS NULL OR "dwc:disposition" IN ('in collection', 'on loan', 'donated', 'exchanged', 'missing', 'destroyed')),
            FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE RESTRICT,
            FOREIGN KEY(preparation_id) REFERENCES preparation (id) ON DELETE RESTRICT,
            FOREIGN KEY(repository_id) REFERENCES repository (id) ON DELETE RESTRICT
        ) STRICT
    """)
    op.execute("""
        INSERT INTO collection_object_old
            (id, collecting_event_id, "dwc:catalogNumber", repository_id,
             "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", "dwc:disposition",
             "dwc:materialEntityRemarks", preparation_id, confidential, created_at, updated_at)
        SELECT co.id, co.collecting_event_id, co."dwc:catalogNumber", co.repository_id,
               co."dwc:basisOfRecord", co."dwc:individualCount", co."dwc:lifeStage",
               (SELECT d.name FROM disposition d WHERE d.id = co.disposition_id),
               co."dwc:materialEntityRemarks", co.preparation_id, co.confidential, co.created_at, co.updated_at
          FROM collection_object co
    """)
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_old RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")
    op.execute("DROP TABLE disposition")
    bind.exec_driver_sql("PRAGMA foreign_key_check")
    bind.exec_driver_sql("PRAGMA foreign_keys = ON")
