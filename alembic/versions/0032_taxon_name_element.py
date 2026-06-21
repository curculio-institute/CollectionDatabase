"""taxon.name_element — atomic name source of truth

Adds the `name_element` column to `taxon`: the rank's own epithet/uninomial
(TaxonWorks' `name`), which becomes the atomic source of truth. dwc:scientificName
remains the *composed* full name (without authorship), maintained from
name_element + the parent chain by compose_scientific_name() — see Epic #30.

Purely additive: a plain ``ALTER TABLE ... ADD COLUMN`` (no table rebuild), so
STRICT typing, CHECK/UNIQUE constraints, FK actions and the synonym-integrity
triggers are all preserved (DB-1 discipline). The column is declared TEXT so it
satisfies STRICT. It is nullable at the DB level (backfilled by import); the
service layer treats it as required.

The retirement of trg_taxon_synonym_parent_matches_accepted lives in a later
migration (Phase 4, #34), together with the synonymize/audit rework.

Revision ID: 0032
Revises: 0031
"""
from typing import Union

from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw DDL (not op.add_column) to guarantee the STRICT-compatible TEXT type
    # rather than SQLAlchemy's default VARCHAR.
    op.execute('ALTER TABLE taxon ADD COLUMN name_element TEXT')


def downgrade() -> None:
    op.execute('ALTER TABLE taxon DROP COLUMN name_element')
