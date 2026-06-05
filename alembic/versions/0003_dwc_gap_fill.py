"""DwC gap-fill: catalogNumber on collection_object, lifeStage, disposition,
georeferencing fields, verbatimCoordinates, taxon epithets, determination fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04

Changes per table
─────────────────
collection_object
  + catalogNumber TEXT          — primary human ID; unique within catalogNamespace
  + catalogNamespace TEXT       — institution/collection prefix (e.g. "Jilg")
  + lifeStage TEXT              — adult / larva / pupa / egg  (DwC Occurrence)
  + disposition TEXT            — "in collection", "on loan", "missing"  (DwC MaterialEntity)
  Partial unique index on (catalogNamespace, catalogNumber) where both non-null.

collecting_event
  + continent TEXT
  + verbatimCoordinates TEXT    — coords as originally recorded (DMS, UTM, …)
  + verbatimCoordinateSystem TEXT
  + coordinatePrecision REAL    — decimal precision of decimalLatitude/Longitude
  + locationRemarks TEXT
  + eventRemarks TEXT
  + georeferencedBy TEXT
  + georeferencedDate TEXT
  + georeferenceProtocol TEXT
  + georeferenceSources TEXT
  + georeferenceRemarks TEXT
  + georeferenceVerificationStatus TEXT

taxon_determination
  + verbatimIdentification TEXT — original ID as written on label
  + identificationReferences TEXT
  + identificationVerificationStatus TEXT

taxon
  + specificEpithet TEXT
  + infraspecificEpithet TEXT
  + taxonomicStatus TEXT        — "accepted", "synonym", …
  + taxonRemarks TEXT
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── collection_object ──────────────────────────────────────────────────
    op.execute("ALTER TABLE collection_object ADD COLUMN catalogNumber TEXT")
    op.execute("ALTER TABLE collection_object ADD COLUMN catalogNamespace TEXT")
    op.execute("ALTER TABLE collection_object ADD COLUMN lifeStage TEXT")
    op.execute("ALTER TABLE collection_object ADD COLUMN disposition TEXT")
    # Partial unique index: only enforced when both fields are present.
    # NULL catalogNumber = uncatalogued; multiple uncatalogued rows are fine.
    op.execute("""
        CREATE UNIQUE INDEX uq_co_catalog
        ON collection_object (catalogNamespace, catalogNumber)
        WHERE catalogNamespace IS NOT NULL AND catalogNumber IS NOT NULL
    """)

    # ── collecting_event ───────────────────────────────────────────────────
    op.execute("ALTER TABLE collecting_event ADD COLUMN continent TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN verbatimCoordinates TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN verbatimCoordinateSystem TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN coordinatePrecision REAL")
    op.execute("ALTER TABLE collecting_event ADD COLUMN locationRemarks TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN eventRemarks TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferencedBy TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferencedDate TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferenceProtocol TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferenceSources TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferenceRemarks TEXT")
    op.execute("ALTER TABLE collecting_event ADD COLUMN georeferenceVerificationStatus TEXT")

    # ── taxon_determination ────────────────────────────────────────────────
    op.execute("ALTER TABLE taxon_determination ADD COLUMN verbatimIdentification TEXT")
    op.execute("ALTER TABLE taxon_determination ADD COLUMN identificationReferences TEXT")
    op.execute("ALTER TABLE taxon_determination ADD COLUMN identificationVerificationStatus TEXT")

    # ── taxon ──────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE taxon ADD COLUMN specificEpithet TEXT")
    op.execute("ALTER TABLE taxon ADD COLUMN infraspecificEpithet TEXT")
    op.execute("ALTER TABLE taxon ADD COLUMN taxonomicStatus TEXT")
    op.execute("ALTER TABLE taxon ADD COLUMN taxonRemarks TEXT")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_co_catalog")

    for col in ("catalogNumber", "catalogNamespace", "lifeStage", "disposition"):
        op.execute(f"ALTER TABLE collection_object DROP COLUMN {col}")

    for col in (
        "continent", "verbatimCoordinates", "verbatimCoordinateSystem",
        "coordinatePrecision", "locationRemarks", "eventRemarks",
        "georeferencedBy", "georeferencedDate", "georeferenceProtocol",
        "georeferenceSources", "georeferenceRemarks", "georeferenceVerificationStatus",
    ):
        op.execute(f"ALTER TABLE collecting_event DROP COLUMN {col}")

    for col in ("verbatimIdentification", "identificationReferences",
                "identificationVerificationStatus"):
        op.execute(f"ALTER TABLE taxon_determination DROP COLUMN {col}")

    for col in ("specificEpithet", "infraspecificEpithet", "taxonomicStatus", "taxonRemarks"):
        op.execute(f"ALTER TABLE taxon DROP COLUMN {col}")
