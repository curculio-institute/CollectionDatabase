"""confidential flags on person / collection_object / collecting_event

A local-only privacy flag (NOT a DwC term, never pushed to TaxonWorks) that
governs what a future DwC export emits:

- a confidential *person* → the record is still exported, but everywhere that
  person appears as recordedBy / identifiedBy the name is replaced with a
  generic string (config.confidential_person_label);
- a confidential *specimen* or *event* → the occurrence is dropped from the
  export entirely (a confidential event withholds its specimens too).

INTEGER 0/1 boolean with a named CHECK, added via native ADD COLUMN (no table
rebuild → STRICT typing + existing CHECK/UNIQUE/FK on the two STRICT tables are
preserved; the named CHECK is column-level so it shows up in sqlite_master and
satisfies the schema-integrity guard).

Revision ID: 0043
Revises: 0042
"""
from typing import Union

from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels = None
depends_on = None

_TABLES = ("person", "collection_object", "collecting_event")
_CK = {
    "person": "ck_person_confidential",
    "collection_object": "ck_co_confidential",
    "collecting_event": "ck_ce_confidential",
}


def upgrade() -> None:
    for t in _TABLES:
        op.execute(
            f"ALTER TABLE {t} ADD COLUMN confidential INTEGER NOT NULL DEFAULT 0 "
            f"CONSTRAINT {_CK[t]} CHECK (confidential IN (0, 1))"
        )


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} DROP COLUMN confidential")
