"""retire the strict synonym-parent rule (Epic #30, Phase 4 / #34)

The atomic-name model parents a name under its *own* lineage: a synonym sits
under its own genus (e.g. *Curculio forticollis* under *Curculio*), not its
accepted name's genus. Validity lives solely in acceptedNameUsageID. This makes
name composition uniform for valid names and synonyms, and makes a status flip
(synonym ↔ valid) a one-field toggle with no name rewrite.

So the project-specific stricter rule "a synonym's parentNameUsageID must equal
its accepted name's parentNameUsageID" is retired: drop both
trg_taxon_synonym_parent_matches_accepted_ins/_upd triggers (migration 0031).

KEPT: trg_taxon_accepted_is_terminal_ins/_upd — GBIF's chained-synonym rule
(acceptedNameUsageID must reference an accepted, terminal name) is still in force.

DB-1 discipline: these triggers live in sqlite_master attached to `taxon` and
are silently dropped by any table rebuild. A DROP here is a plain DDL statement
(no rebuild), so the surviving terminal triggers are untouched.

Revision ID: 0033
Revises: 0032
"""
from typing import Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels = None
depends_on = None


# Bodies copied from migration 0031 so downgrade restores them verbatim.
_PARENT_MATCH_BODY = """
WHEN NEW."dwc:acceptedNameUsageID" IS NOT NULL
 AND NEW."dwc:parentNameUsageID" IS NOT (
       SELECT "dwc:parentNameUsageID" FROM taxon WHERE id = NEW."dwc:acceptedNameUsageID")
BEGIN
  SELECT RAISE(ABORT,
    'synonym parentNameUsageID must equal its accepted name''s parentNameUsageID');
END;
"""


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_taxon_synonym_parent_matches_accepted_ins")
    op.execute("DROP TRIGGER IF EXISTS trg_taxon_synonym_parent_matches_accepted_upd")


def downgrade() -> None:
    op.execute(f'CREATE TRIGGER trg_taxon_synonym_parent_matches_accepted_ins '
               f'BEFORE INSERT ON taxon{_PARENT_MATCH_BODY}')
    op.execute(f'CREATE TRIGGER trg_taxon_synonym_parent_matches_accepted_upd '
               f'BEFORE UPDATE ON taxon{_PARENT_MATCH_BODY}')
