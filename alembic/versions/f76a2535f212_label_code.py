"""label_code

Revision ID: f76a2535f212
Revises: d67db47af8e8
Create Date: 2026-06-04 20:46:49.561179

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f76a2535f212'
down_revision: Union[str, None] = 'd67db47af8e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE label_code (
            id          INTEGER PRIMARY KEY,
            code        TEXT    NOT NULL UNIQUE,
            status      TEXT    NOT NULL DEFAULT 'reserved'
                            CHECK (status IN ('reserved', 'assigned')),
            collection_object_id INTEGER
                            REFERENCES collection_object(id) ON DELETE SET NULL,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        ) STRICT
    """)


def downgrade() -> None:
    op.drop_table("label_code")
