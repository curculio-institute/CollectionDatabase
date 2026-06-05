"""Restructure taxon table: DwC parent-link model (GBIF checklist best practices).

Replaces denormalised rank columns (dwc:order, dwc:family, dwc:subfamily,
dwc:tribe, dwc:subtribe, dwc:genus, dwc:subgenus, dwc:specificEpithet,
dwc:infraspecificEpithet) and the non-DwC rank-authorship columns with the
minimal DwC Taxon core fields:

  dwc:scientificName          – bare name without authorship
  dwc:taxonRank               – rank string (species, genus, family …)
  dwc:taxonomicStatus         – "accepted" | "synonym"
  dwc:parentNameUsageID       – FK to taxon.id (replaces parent_id)

Kept unchanged:
  dwc:scientificNameAuthorship
  dwc:acceptedNameUsageID
  taxonworksOtuID

All existing taxon rows and dependent rows (taxon_determination,
biological_association with taxon references) are wiped so the table can be
re-imported using the updated logic.

Revision ID: 0012
Revises: 03e9ac24497c
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "03e9ac24497c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Disable FK enforcement so we can drop tables freely.
    bind.execute(sa.text("PRAGMA foreign_keys = OFF"))

    # Wipe dependent rows first, then taxon.
    bind.execute(sa.text("DELETE FROM taxon_determination"))
    bind.execute(sa.text(
        "DELETE FROM biological_association "
        "WHERE subject_taxon_id IS NOT NULL OR object_taxon_id IS NOT NULL"
    ))
    bind.execute(sa.text("DELETE FROM taxon"))

    # Rebuild taxon table with DwC parent-link schema.
    bind.execute(sa.text("DROP TABLE taxon"))
    bind.execute(sa.text("""
        CREATE TABLE taxon (
            id                              INTEGER PRIMARY KEY,
            "dwc:scientificName"            TEXT NOT NULL,
            "dwc:taxonRank"                 TEXT NOT NULL,
            "dwc:taxonomicStatus"           TEXT NOT NULL
                                                CHECK ("dwc:taxonomicStatus" IN ('accepted', 'synonym')),
            "dwc:scientificNameAuthorship"  TEXT,
            "dwc:parentNameUsageID"         INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
            "dwc:acceptedNameUsageID"       INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
            "taxonworksOtuID"               INTEGER,
            created_at                      TEXT NOT NULL,
            updated_at                      TEXT NOT NULL
        ) STRICT
    """))
    bind.execute(sa.text(
        'CREATE INDEX ix_taxon_parent_name_usage_id ON taxon ("dwc:parentNameUsageID")'
    ))

    bind.execute(sa.text("PRAGMA foreign_keys = ON"))


def downgrade() -> None:
    raise NotImplementedError(
        "0012 is a destructive migration (data wiped). Manual restore required."
    )
