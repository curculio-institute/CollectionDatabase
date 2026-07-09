"""taxon.ipniID — the IPNI identifier of the name a taxon was imported from (#98, #99)

The bare IPNI id (WCVP's `scientificnameid`, e.g. `ipni:304293-2`, stored as `304293-2`).
Sits alongside `taxonworksOtuID`, which plays exactly the same external-identity role for
TaxonWorks — and follows the same naming convention: a **source-specific** external id is a
plain camelCase local column, not a `dwc:` term. (An earlier draft named this
`dwc:scientificNameID`. That DwC term is deliberately generic — "an identifier for the
nomenclatural details of a scientific name" — and could hold a WFO, IPNI or other id. This
column only ever holds an IPNI id, so it is named for what it is.)

This is **identity, not provenance**: it records *which name this is*, not whose opinion the
row reflects. Re-parenting or re-linking the name by hand leaves it true — which is why a
`dwc:nameAccordingTo` column was rejected (it would become false the moment the row is
edited) and this one is not. The DB is the source of truth and does not claim to follow any
WCVP release; see docs/plant_names.md.

Nullable: TaxonWorks imports, manual creations, and the 0.9% of WCVP accepted names without
an IPNI id all leave it NULL. It can only be captured at import, so names imported before
this column existed are matchable only by name + authorship (#99).

Native ADD COLUMN (no table rebuild → STRICT typing, both self-FKs with ON DELETE RESTRICT,
ix_taxon_parent_name_usage_id, and the two trg_taxon_accepted_is_terminal triggers are all
preserved; CLAUDE.md migration discipline).

Revision ID: 0053
Revises: 0052
"""
from typing import Union

from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE taxon ADD COLUMN "ipniID" TEXT')


def downgrade() -> None:
    op.execute('ALTER TABLE taxon DROP COLUMN "ipniID"')
