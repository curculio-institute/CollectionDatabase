"""Add dwc:nomenclaturalCode to taxon; replace biological_relationship seed rows with TW data.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-06

Schema change:
  taxon: add "dwc:nomenclaturalCode" TEXT column (nullable, no backfill needed).

Data change:
  biological_relationship: delete the 5 placeholder rows inserted in migration 0001
  (collected_on, feeds_on, parasitizes, reared_from, associated_with — which have no
  matching taxonworksID and do not correspond to any real TW relationship).  Insert
  the 15 actual BiologicalRelationship rows from TW project 40, with taxonworksID set
  to the stable TW integer PKs.  These IDs are stable project-scoped values verified
  against sfg.taxonworks.org on 2026-06-06.

  There are zero BiologicalAssociation rows in the DB at migration time so no FK
  references to the old rows exist.  No matching logic needed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = "datetime('now')"

# TW biological_relationships from project 40, verified 2026-06-06.
# Sorted: active relationships first, [legacy] last.
_TW_RELATIONSHIPS = [
    ("collected from",                                 92),
    ("feeding observed in experimental setup on",     104),
    ("feeding observed in the wild on",                95),
    ("reared from",                                    97),
    ("reared from galls on",                          101),
    ("undefined relationship with",                   103),
    ("[legacy] endophagous larva feeds in roots of",   96),
    ("[legacy] feeding observed on",                  102),
    ("[legacy] feeds on",                              56),
    ("[legacy] is infected with",                      61),
    ("[legacy] is mutualistically associated with",    59),
    ("[legacy] is parasitized by",                     57),
    ("[legacy] is vector of",                          60),
    ("[legacy] is visitor of",                         58),
    ("[legacy] oviposited on",                        100),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add nomenclaturalCode column to taxon.
    bind.execute(sa.text(
        'ALTER TABLE taxon ADD COLUMN "dwc:nomenclaturalCode" TEXT'
    ))

    # 2. Replace placeholder biological_relationship rows.
    bind.execute(sa.text("DELETE FROM biological_relationship"))
    for name, tw_id in _TW_RELATIONSHIPS:
        bind.execute(sa.text(
            "INSERT INTO biological_relationship (name, taxonworksID, created_at, updated_at) "
            f"VALUES (:name, :tw_id, {_NOW}, {_NOW})"
        ), {"name": name, "tw_id": tw_id})


def downgrade() -> None:
    bind = op.get_bind()

    # Restore the 5 original placeholder rows (no taxonworksID).
    bind.execute(sa.text("DELETE FROM biological_relationship"))
    for name in ("collected_on", "feeds_on", "parasitizes", "reared_from", "associated_with"):
        bind.execute(sa.text(
            f"INSERT INTO biological_relationship (name, created_at, updated_at) "
            f"VALUES (:name, {_NOW}, {_NOW})"
        ), {"name": name})

    # SQLite does not support DROP COLUMN on older versions; use batch mode.
    with op.batch_alter_table("taxon") as b:
        b.drop_column("dwc:nomenclaturalCode")
