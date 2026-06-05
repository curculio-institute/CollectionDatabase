"""Simplify taxon and taxon_determination

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-04

taxon
  Dropped: dwc:kingdom, dwc:phylum, dwc:class, dwc:taxonRank, dwc:taxonRemarks,
           dwc:scientificName (derived at export: genus + specificEpithet + authorship)
  Kept:    dwc:order, dwc:family, dwc:subfamily, dwc:tribe, dwc:subtribe,
           dwc:genus, dwc:subgenus, dwc:specificEpithet, dwc:infraspecificEpithet,
           dwc:scientificNameAuthorship, dwc:taxonomicStatus, parent_id

taxon_determination
  Dropped: dwc:identificationReferences, dwc:identificationVerificationStatus
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TAXON_DDL = """
    CREATE TABLE {name} (
        id                              INTEGER PRIMARY KEY,
        "dwc:order"                     TEXT,
        "dwc:family"                    TEXT,
        "dwc:subfamily"                 TEXT,
        "dwc:tribe"                     TEXT,
        "dwc:subtribe"                  TEXT,
        "dwc:genus"                     TEXT,
        "dwc:subgenus"                  TEXT,
        "dwc:specificEpithet"           TEXT,
        "dwc:infraspecificEpithet"      TEXT,
        "dwc:scientificNameAuthorship"  TEXT,
        "dwc:taxonomicStatus"           TEXT,
        parent_id                       INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL
    ) STRICT"""

_TAXON_COLS = """\
    "dwc:order", "dwc:family", "dwc:subfamily", "dwc:tribe", "dwc:subtribe",
    "dwc:genus", "dwc:subgenus", "dwc:specificEpithet", "dwc:infraspecificEpithet",
    "dwc:scientificNameAuthorship", "dwc:taxonomicStatus", parent_id, created_at, updated_at"""


def upgrade() -> None:
    # ── taxon: full table rebuild ──────────────────────────────────────────
    op.execute("DROP TABLE IF EXISTS taxon_new")
    op.execute(_TAXON_DDL.format(name="taxon_new"))
    op.execute(f"""
        INSERT INTO taxon_new (id, {_TAXON_COLS})
        SELECT id, {_TAXON_COLS} FROM taxon
    """)
    op.execute("DROP TABLE taxon")
    op.execute("ALTER TABLE taxon_new RENAME TO taxon")
    op.execute("CREATE INDEX ix_taxon_parent_id ON taxon (parent_id)")

    # ── taxon_determination: drop two columns ──────────────────────────────
    op.execute('ALTER TABLE taxon_determination DROP COLUMN "dwc:identificationReferences"')
    op.execute('ALTER TABLE taxon_determination DROP COLUMN "dwc:identificationVerificationStatus"')


def downgrade() -> None:
    op.execute('ALTER TABLE taxon_determination ADD COLUMN "dwc:identificationReferences" TEXT')
    op.execute('ALTER TABLE taxon_determination ADD COLUMN "dwc:identificationVerificationStatus" TEXT')

    op.execute("DROP TABLE IF EXISTS taxon_old")
    op.execute("""
        CREATE TABLE taxon_old (
            id                              INTEGER PRIMARY KEY,
            "dwc:scientificName"            TEXT NOT NULL,
            "dwc:scientificNameAuthorship"  TEXT,
            "dwc:taxonRank"                 TEXT,
            "dwc:specificEpithet"           TEXT,
            "dwc:infraspecificEpithet"      TEXT,
            "dwc:taxonomicStatus"           TEXT,
            "dwc:taxonRemarks"              TEXT,
            parent_id                       INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
            "dwc:kingdom" TEXT, "dwc:phylum" TEXT, "dwc:class" TEXT, "dwc:order" TEXT,
            "dwc:family" TEXT, "dwc:subfamily" TEXT, "dwc:tribe" TEXT, "dwc:subtribe" TEXT,
            "dwc:genus" TEXT, "dwc:subgenus" TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        ) STRICT
    """)
    op.execute(f"""
        INSERT INTO taxon_old (
            id, "dwc:scientificNameAuthorship", "dwc:specificEpithet",
            "dwc:infraspecificEpithet", "dwc:taxonomicStatus", parent_id,
            "dwc:order", "dwc:family", "dwc:subfamily", "dwc:tribe", "dwc:subtribe",
            "dwc:genus", "dwc:subgenus", created_at, updated_at
        )
        SELECT
            id, "dwc:scientificNameAuthorship", "dwc:specificEpithet",
            "dwc:infraspecificEpithet", "dwc:taxonomicStatus", parent_id,
            "dwc:order", "dwc:family", "dwc:subfamily", "dwc:tribe", "dwc:subtribe",
            "dwc:genus", "dwc:subgenus", created_at, updated_at
        FROM taxon
    """)
    op.execute("DROP TABLE taxon")
    op.execute("ALTER TABLE taxon_old RENAME TO taxon")
    op.execute("CREATE INDEX ix_taxon_parent_id ON taxon (parent_id)")
