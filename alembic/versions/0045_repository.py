"""repository — institutions / collections lookup (#56)

A small controlled-vocabulary table mapping a collection code to its full names and
its TaxonWorks ids. Used by the identifier label (resolve the code prefix →
collection_full_name) and, later, by the DwC export / TW sync.

Columns that map directly to Darwin Core carry the project's ``dwc:`` prefix so the
export is a straight passthrough. The full names + TW ids have no DwC term and stay
local. TaxonWorks has separate ids for the institution (Repository) and the
collection (Namespace), so both are stored.

Revision ID: 0045
Revises: 0044
"""
from typing import Union

from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE repository (
            id                         INTEGER PRIMARY KEY,
            "dwc:institutionCode"      TEXT,
            institution_full_name      TEXT,
            "dwc:collectionCode"       TEXT NOT NULL,
            collection_full_name       TEXT NOT NULL,
            taxonworks_institution_id  INTEGER,
            taxonworks_collection_id   INTEGER,
            created_at                 TEXT NOT NULL,
            updated_at                 TEXT NOT NULL,
            UNIQUE("dwc:collectionCode")
        ) STRICT
    """)


def downgrade() -> None:
    op.execute("DROP TABLE repository")
