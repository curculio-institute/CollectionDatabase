"""Drop identifier table

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-04

catalogNumber + catalogNamespace are now directly on collection_object.
occurrenceID is derived at DwC export time from catalogNumber.
fieldNumber is on collecting_event.
A future twOccurrenceID column can be added to collection_object if needed
after TaxonWorks sync is implemented.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE identifier")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE identifier (
            id                    INTEGER PRIMARY KEY,
            collection_object_id  INTEGER NOT NULL REFERENCES collection_object(id) ON DELETE CASCADE,
            namespace             TEXT NOT NULL,
            identifier            TEXT NOT NULL,
            identifier_type       TEXT NOT NULL,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            UNIQUE (namespace, identifier)
        ) STRICT
    """)
    op.execute("CREATE INDEX ix_identifier_co_id ON identifier (collection_object_id)")
