"""export_settings — remembers the TaxonWorks export's taxonomy restriction (#149)

A single-row table holding the taxon the last "Check collection" run was restricted to
(e.g. "Curculionoidea only"), so the Export tab remembers the restriction across restarts
instead of defaulting back to "everything" every time. Mirrors ``person_defaults``
(migration 0022) — also not STRICT, a tiny settings row rather than collection data, and
not modelled as an ORM class for the same reason (`app/services/person_defaults.py`'s own
comment: one column is not worth a mapped class; accessed via raw SQL in
`app/services/export_settings.py`).

Unlike a person default (FK `ON DELETE RESTRICT` — an active default must not be silently
orphaned, see CLAUDE.md "Why person defaults live in the DB"), this restriction is not a
value anything depends on existing: if the scoping taxon is later deleted or merged away,
silently falling back to "no restriction" is the safe, honest behaviour (the alternative,
leaving a dangling id, would silently narrow every future export to nothing). So the FK is
`ON DELETE SET NULL`, the same choice already used for `import_dataset_record.taxon_id`
(deleting the entity forgets the link without deleting the audit trail).

Revision ID: 0067
Revises: 0066
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "tw_export_taxon_id", sa.Integer,
            sa.ForeignKey("taxon.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.execute(
        "INSERT INTO export_settings (id, tw_export_taxon_id) VALUES (1, NULL)"
    )


def downgrade() -> None:
    op.drop_table("export_settings")
