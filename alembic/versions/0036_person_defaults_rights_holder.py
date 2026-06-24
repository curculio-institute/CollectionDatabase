"""person_defaults.default_rights_holder_id — Tier-2 default for media rightsHolder (#48)

Adds a third configurable person default (alongside default_identified_by_id /
default_recorded_by_id) so the media metadata editor's rightsHolder field can offer a
one-click default. FK → person(id) ON DELETE RESTRICT, so it is delete-safe and is
re-pointed automatically by merge_persons (dynamic _fk_references_to_person discovery,
which matches FKs to person.id). person_defaults is not STRICT (see schema notes).

Revision ID: 0036
Revises: 0035
"""
from typing import Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    cols = [c["name"] for c in inspect(op.get_bind()).get_columns(table)]
    return col in cols


def upgrade() -> None:
    if not _col_exists("person_defaults", "default_rights_holder_id"):
        op.execute(
            "ALTER TABLE person_defaults "
            "ADD COLUMN default_rights_holder_id INTEGER REFERENCES person(id) ON DELETE RESTRICT"
        )


def downgrade() -> None:
    if _col_exists("person_defaults", "default_rights_holder_id"):
        op.execute("ALTER TABLE person_defaults DROP COLUMN default_rights_holder_id")
