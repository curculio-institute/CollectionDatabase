"""drop export_settings — the table nothing ever read (#149)

Migration 0067 created a single-row `export_settings` table to remember the taxon the
TaxonWorks export was last restricted to, across restarts. That feature was then
deliberately cut: `app/ui/tw_sync_tab.py`'s own docstring records the decision — the
restriction is **session-only**, cleared on page reload, because a migration plus a table
is `person_defaults`-level ceremony for a UI convenience nothing else depends on. The
service 0067's docstring names, `app/services/export_settings.py`, was never written.

So the table shipped orphaned: no code reads or writes it, while `docs/schema.html`
described persistence the application does not have. Dropping it rather than implementing
the service keeps the schema an honest description of what the app does — and the table
holds nothing (one row, a NULL taxon id), so there is no data to lose. If the setting is
ever wanted, it comes back as its own migration alongside the service that uses it.

0067 is left untouched: it is already applied to the live database, and schema changes go
through a new migration, never a hand-edit of an applied one (CLAUDE.md §8).

Revision ID: 0068
Revises: 0067
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("export_settings")


def downgrade() -> None:
    # Recreate exactly what 0067 built, so a downgrade/upgrade cycle round-trips.
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
