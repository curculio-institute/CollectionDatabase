"""print_queue.text_override — per-row print-only label text (#37)

The Print queue lets the user adjust a label *for printing only* — abbreviate
text too long for the tiny label, or add something the auto-format omits —
without touching the specimen/event record (the record stays master, edited in
Records). The override is stored per queue row in a new nullable TEXT column.

Applies to 'data' and 'determination' rows; 'identifier' rows are never edited
(immutable catalog number).

Uses SQLite's native ``ALTER TABLE ADD COLUMN`` (no table rebuild), so the
STRICT typing and both CHECK constraints on print_queue are preserved untouched
(DB-1 / migration discipline — a batch recreate would have dropped them).

Revision ID: 0034
Revises: 0033
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("print_queue", sa.Column("text_override", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_queue", "text_override")
