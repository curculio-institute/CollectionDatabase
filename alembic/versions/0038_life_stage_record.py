"""life_stage_record — reared-specimen life-stage history (#50)

One STRICT table: per-specimen additional (dwc:lifeStage, dwc:basisOfRecord, dwc:eventDate)
rows recording earlier life stages of the same reared individual (e.g. the wild larva),
without duplicating specimen or event rows. FK → collection_object ON DELETE CASCADE;
basisOfRecord CHECK mirrors collection_object's.

Raw ``CREATE TABLE … STRICT`` DDL so STRICT + CHECK + FK action are explicit and survive
(CLAUDE.md migration discipline; guarded by tests/test_schema_integrity.py).

Revision ID: 0038
Revises: 0037
"""
from typing import Union

from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE life_stage_record (
            id                   INTEGER PRIMARY KEY,
            collection_object_id INTEGER NOT NULL REFERENCES collection_object(id) ON DELETE CASCADE,
            "dwc:lifeStage"      TEXT NOT NULL,
            "dwc:basisOfRecord"  TEXT NOT NULL DEFAULT 'HumanObservation'
                                     CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen','FossilSpecimen','HumanObservation')),
            "dwc:eventDate"      TEXT,
            sort_order           INTEGER NOT NULL DEFAULT 0,
            remarks              TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        ) STRICT
    """)


def downgrade() -> None:
    op.drop_table("life_stage_record")
