"""drop taxon.taxonomicStatus — synonymy is encoded solely by acceptedNameUsageID

`dwc:taxonomicStatus` ("accepted" | "synonym") is fully derivable from
`dwc:acceptedNameUsageID` (a row is a synonym iff it links to an accepted name).
Storing both lets them drift — and one row had already drifted (an "accepted"
taxon carrying an accepted-name link). The DwC Taxon-core term is now *derived*
at export time instead of stored.

(History: the column was first dropped in 0011 as redundant, restored in 0012
for DwC compliance; this migration drops the *storage* again but keeps the term
available via derivation. See CLAUDE.md §4.)

The column is referenced by a CHECK constraint, so SQLite cannot ALTER TABLE
DROP COLUMN it — the table is rebuilt. Per the DB-1 lesson, the rebuild
re-declares STRICT, the two self-FK ON DELETE RESTRICT actions, NOT NULL
columns, and the parent-link index verbatim. tests/test_schema_integrity.py
guards against any constraint loss.

Revision ID: 0030
Revises: 0029
"""
from typing import Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute("""CREATE TABLE taxon_new (
        	id INTEGER NOT NULL,
        	"dwc:scientificName" TEXT NOT NULL,
        	"dwc:taxonRank" TEXT NOT NULL,
        	"dwc:scientificNameAuthorship" TEXT,
        	"dwc:parentNameUsageID" INTEGER,
        	"dwc:acceptedNameUsageID" INTEGER,
        	"taxonworksOtuID" INTEGER,
        	"dwc:nomenclaturalCode" TEXT,
        	created_at TEXT NOT NULL,
        	updated_at TEXT NOT NULL,
        	PRIMARY KEY (id),
        	FOREIGN KEY("dwc:parentNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT,
        	FOREIGN KEY("dwc:acceptedNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute('INSERT INTO taxon_new (id, "dwc:scientificName", "dwc:taxonRank", "dwc:scientificNameAuthorship", "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID", "dwc:nomenclaturalCode", created_at, updated_at) SELECT id, "dwc:scientificName", "dwc:taxonRank", "dwc:scientificNameAuthorship", "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID", "dwc:nomenclaturalCode", created_at, updated_at FROM taxon')
        op.execute("DROP TABLE taxon")
        op.execute("ALTER TABLE taxon_new RENAME TO taxon")
        op.execute('CREATE INDEX ix_taxon_parent_name_usage_id ON taxon ("dwc:parentNameUsageID")')

        rows = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
        if rows:
            raise RuntimeError(f"FK check failed after dropping taxonomicStatus: {rows}")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    # Re-add the column, defaulting every row to 'accepted' then marking rows
    # that carry an accepted-name link as 'synonym' (the same derivation the
    # export now performs). Rebuild to restore STRICT + the CHECK constraint.
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute("""CREATE TABLE taxon_new (
        	id INTEGER NOT NULL,
        	"dwc:scientificName" TEXT NOT NULL,
        	"dwc:taxonRank" TEXT NOT NULL,
        	"dwc:taxonomicStatus" TEXT NOT NULL
        		CHECK ("dwc:taxonomicStatus" IN ('accepted', 'synonym')),
        	"dwc:scientificNameAuthorship" TEXT,
        	"dwc:parentNameUsageID" INTEGER,
        	"dwc:acceptedNameUsageID" INTEGER,
        	"taxonworksOtuID" INTEGER,
        	"dwc:nomenclaturalCode" TEXT,
        	created_at TEXT NOT NULL,
        	updated_at TEXT NOT NULL,
        	PRIMARY KEY (id),
        	FOREIGN KEY("dwc:parentNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT,
        	FOREIGN KEY("dwc:acceptedNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute("""INSERT INTO taxon_new (id, "dwc:scientificName", "dwc:taxonRank", "dwc:taxonomicStatus", "dwc:scientificNameAuthorship", "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID", "dwc:nomenclaturalCode", created_at, updated_at) SELECT id, "dwc:scientificName", "dwc:taxonRank", CASE WHEN "dwc:acceptedNameUsageID" IS NULL THEN 'accepted' ELSE 'synonym' END, "dwc:scientificNameAuthorship", "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID", "dwc:nomenclaturalCode", created_at, updated_at FROM taxon""")
        op.execute("DROP TABLE taxon")
        op.execute("ALTER TABLE taxon_new RENAME TO taxon")
        op.execute('CREATE INDEX ix_taxon_parent_name_usage_id ON taxon ("dwc:parentNameUsageID")')

        rows = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
        if rows:
            raise RuntimeError(f"FK check failed after restoring taxonomicStatus: {rows}")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")
