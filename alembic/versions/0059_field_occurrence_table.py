"""field_occurrence — HumanObservation records (host plants, standalone sightings)

A dedicated STRICT table for things recorded but NOT physically collected, mirroring
TaxonWorks' FieldOccurrence (the sibling of CollectionObject). See
docs/field_occurrence.md. Unlike collection_object it has NO catalog_number and NO
repository/preparation/disposition — nothing is held; identity is its own
``dwc:occurrenceID`` (a UUID), so the specimen catalog-number invariant is untouched.

Raw ``CREATE TABLE … STRICT`` DDL so STRICT + every CHECK/UNIQUE + the FK action are
explicit and survive (CLAUDE.md migration discipline; guarded by
tests/test_schema_integrity.py).

Revision ID: 0059
Revises: 0058
"""
from typing import Union

from alembic import op

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE field_occurrence (
            id                     INTEGER PRIMARY KEY,
            "dwc:occurrenceID"     TEXT NOT NULL,
            collecting_event_id    INTEGER NOT NULL REFERENCES collecting_event(id) ON DELETE RESTRICT,
            "dwc:basisOfRecord"    TEXT NOT NULL DEFAULT 'HumanObservation',
            "dwc:individualCount"  INTEGER NOT NULL DEFAULT 1,
            "dwc:sex"              TEXT,
            "dwc:lifeStage"        TEXT,
            "dwc:occurrenceRemarks" TEXT,
            confidential           INTEGER NOT NULL DEFAULT 0,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL,
            CONSTRAINT uq_fo_occurrence_id UNIQUE ("dwc:occurrenceID"),
            CONSTRAINT ck_fo_individual_count_non_negative CHECK ("dwc:individualCount" >= 0),
            CONSTRAINT ck_fo_basis_of_record CHECK (
                "dwc:basisOfRecord" IN ('HumanObservation', 'MachineObservation')
            ),
            CONSTRAINT ck_fo_confidential CHECK (confidential IN (0, 1))
        ) STRICT
    """)
    op.execute(
        "CREATE INDEX ix_fo_collecting_event_id ON field_occurrence (collecting_event_id)")


def downgrade() -> None:
    op.drop_table("field_occurrence")
