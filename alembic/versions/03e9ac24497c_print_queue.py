"""print_queue

Revision ID: 03e9ac24497c
Revises: cc96166a829c
Create Date: 2026-06-04 22:13:15.804287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '03e9ac24497c'
down_revision: Union[str, None] = 'cc96166a829c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE print_queue (
            id                   INTEGER PRIMARY KEY,
            label_type           TEXT    NOT NULL
                                     CHECK (label_type IN ('data','determination','identifier')),
            collection_object_id INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            label_code_id        INTEGER REFERENCES label_code(id)        ON DELETE CASCADE,
            created_at           TEXT    NOT NULL,
            updated_at           TEXT    NOT NULL
        ) STRICT
    """)


def downgrade() -> None:
    op.drop_table("print_queue")
