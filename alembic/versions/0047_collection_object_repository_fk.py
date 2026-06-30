"""collection_object: replace collectionCode/institutionCode text with a repository_id FK (#75, parent #72)

Collection membership stops being denormalised text and becomes a single FK to the
``repository`` table — the source of truth for the owning institution/collection. This
*dissolves* the original #72 bug: a rename resolves live through the FK (nothing to
cascade) and a delete is blocked by ON DELETE RESTRICT (no code guard needed).

- DROP ``dwc:collectionCode`` + ``dwc:institutionCode``.
- ADD ``repository_id INTEGER NOT NULL REFERENCES repository(id) ON DELETE RESTRICT``.
- The catalog-number uniqueness scope moves from (collectionCode, catalogNumber) to
  (repository_id, catalogNumber) — a catalog number is unique within its owning
  collection (foreign datasets may reuse numbers under their own repository).

``collectionCode`` is part of the UNIQUE table-constraint, so it cannot be dropped with
a native DROP COLUMN — the STRICT table is rebuilt by hand (the 0029 recipe: PRAGMA
foreign_keys=OFF, build _new, copy, drop, rename, re-create indexes), re-declaring
*every* STRICT/CHECK/UNIQUE/FK/DEFAULT per CLAUDE.md migration discipline. Child tables
reference collection_object by name, so after the rename their FKs stay valid — only
this one table is rebuilt.

Backfill: a repository row is created for every distinct collectionCode still in use
(from collectionCode + institutionCode) before the copy, so repository_id always
resolves (NOT NULL). The live DB is empty at this revision, so the backfill is a no-op.

Revision ID: 0047
Revises: 0046
"""
from typing import Union

from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels = None
depends_on = None

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")

    # 1. Ensure a repository exists for every collectionCode still in use, so the
    #    repository_id backfill below always resolves (NOT NULL). No-op on empty DB.
    op.execute(f"""
        INSERT OR IGNORE INTO repository
            ("dwc:collectionCode", collection_full_name, "dwc:institutionCode", created_at, updated_at)
        SELECT DISTINCT "dwc:collectionCode", "dwc:collectionCode",
               NULLIF("dwc:institutionCode", ''), {_NOW}, {_NOW}
          FROM collection_object
         WHERE "dwc:collectionCode" NOT IN (SELECT "dwc:collectionCode" FROM repository)
    """)

    # 2. Rebuild collection_object with repository_id replacing the two text columns.
    op.execute("""
        CREATE TABLE collection_object_new (
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
        INSERT INTO collection_object_new
            (id, collecting_event_id, "dwc:catalogNumber", repository_id,
             "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", "dwc:disposition",
             "dwc:materialEntityRemarks", preparation_id, confidential, created_at, updated_at)
        SELECT co.id, co.collecting_event_id, co."dwc:catalogNumber",
               (SELECT r.id FROM repository r WHERE r."dwc:collectionCode" = co."dwc:collectionCode"),
               co."dwc:basisOfRecord", co."dwc:individualCount", co."dwc:lifeStage", co."dwc:disposition",
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
            "dwc:collectionCode" TEXT NOT NULL,
            "dwc:institutionCode" TEXT DEFAULT '' NOT NULL,
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
            CONSTRAINT uq_co_collection_catalog UNIQUE ("dwc:collectionCode", "dwc:catalogNumber"),
            CONSTRAINT ck_co_individual_count_non_negative CHECK ("dwc:individualCount" >= 0),
            CONSTRAINT ck_co_basis_of_record CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')),
            CONSTRAINT ck_co_disposition CHECK ("dwc:disposition" IS NULL OR "dwc:disposition" IN ('in collection', 'on loan', 'donated', 'exchanged', 'missing', 'destroyed')),
            FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE RESTRICT,
            FOREIGN KEY(preparation_id) REFERENCES preparation (id) ON DELETE RESTRICT
        ) STRICT
    """)
    op.execute("""
        INSERT INTO collection_object_old
            (id, collecting_event_id, "dwc:catalogNumber", "dwc:collectionCode", "dwc:institutionCode",
             "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", "dwc:disposition",
             "dwc:materialEntityRemarks", preparation_id, confidential, created_at, updated_at)
        SELECT co.id, co.collecting_event_id, co."dwc:catalogNumber",
               (SELECT r."dwc:collectionCode" FROM repository r WHERE r.id = co.repository_id),
               COALESCE((SELECT r."dwc:institutionCode" FROM repository r WHERE r.id = co.repository_id), ''),
               co."dwc:basisOfRecord", co."dwc:individualCount", co."dwc:lifeStage", co."dwc:disposition",
               co."dwc:materialEntityRemarks", co.preparation_id, co.confidential, co.created_at, co.updated_at
          FROM collection_object co
    """)
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_old RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")
    bind.exec_driver_sql("PRAGMA foreign_key_check")
    bind.exec_driver_sql("PRAGMA foreign_keys = ON")
