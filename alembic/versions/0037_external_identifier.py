"""external_identifier — external resource IDs (e.g. iNaturalist URL) for specimens &
associations (#49)

One STRICT table: an external link/identifier attached to exactly one of a
collection_object or a biological_association (exclusive-arc CHECK; both FKs ON DELETE
CASCADE). For an association it denotes the *other party* as an external resource — an
optional addition; the biological_association object arc is left unchanged.

Raw ``CREATE TABLE … STRICT`` DDL so STRICT + the CHECK + FK actions are explicit and
survive (CLAUDE.md migration discipline; guarded by tests/test_schema_integrity.py).

Revision ID: 0037
Revises: 0036
"""
from typing import Union

from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE external_identifier (
            id                        INTEGER PRIMARY KEY,
            collection_object_id      INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            biological_association_id INTEGER REFERENCES biological_association(id) ON DELETE CASCADE,
            source                    TEXT,
            value                     TEXT NOT NULL,
            label                     TEXT,
            remarks                   TEXT,
            created_at                TEXT NOT NULL,
            updated_at                TEXT NOT NULL,
            CONSTRAINT ck_external_identifier_exclusive_arc CHECK (
                (collection_object_id IS NOT NULL AND biological_association_id IS NULL) OR
                (collection_object_id IS NULL AND biological_association_id IS NOT NULL)
            )
        ) STRICT
    """)


def downgrade() -> None:
    op.drop_table("external_identifier")
