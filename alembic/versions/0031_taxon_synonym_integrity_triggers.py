"""taxon synonym-integrity guard triggers

Two loud BEFORE INSERT/UPDATE triggers that make the synonym-side bad state
unreachable from *any* write path (raw SQL included), each RAISE(ABORT)ing:

  * trg_taxon_synonym_parent_matches_accepted — a synonym's parentNameUsageID
    must equal its accepted name's parentNameUsageID (in this model the
    classification lives on the concept, so a name and its synonyms share a
    parent). NULL-safe via `IS NOT`.
  * trg_taxon_accepted_is_terminal — acceptedNameUsageID must reference an
    accepted name, never another synonym. This is GBIF's "chained synonym"
    rule (NameUsageIssue): synonym chains are not allowed; acceptedNameUsageID
    points to the terminal accepted name.

The cascades that *keep* these true across multi-row edits (re-parenting,
synonymising a name that has its own synonyms) live in the service layer
(synonymize / reparent), not in triggers — see CLAUDE.md. These triggers are
the loud backstop; verify_taxon_consistency() is the manual audit for drift the
triggers can't catch at write time (e.g. a raw-SQL re-parent of an accepted
name, which never touches the stale synonym rows).

NB (DB-1 discipline): triggers live in sqlite_master attached to the table and
are SILENTLY DROPPED by any taxon table rebuild. Any future migration that
rebuilds `taxon` MUST re-create these. tests/test_schema_integrity.py asserts
they exist.

Revision ID: 0031
Revises: 0030
"""
from typing import Union

from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels = None
depends_on = None

_TRIGGERS = [
    # (name, event)
    ("trg_taxon_synonym_parent_matches_accepted_ins", "BEFORE INSERT"),
    ("trg_taxon_synonym_parent_matches_accepted_upd", "BEFORE UPDATE"),
    ("trg_taxon_accepted_is_terminal_ins", "BEFORE INSERT"),
    ("trg_taxon_accepted_is_terminal_upd", "BEFORE UPDATE"),
]

_PARENT_MATCH_BODY = """
WHEN NEW."dwc:acceptedNameUsageID" IS NOT NULL
 AND NEW."dwc:parentNameUsageID" IS NOT (
       SELECT "dwc:parentNameUsageID" FROM taxon WHERE id = NEW."dwc:acceptedNameUsageID")
BEGIN
  SELECT RAISE(ABORT,
    'synonym parentNameUsageID must equal its accepted name''s parentNameUsageID');
END;
"""

_TERMINAL_BODY = """
WHEN NEW."dwc:acceptedNameUsageID" IS NOT NULL
 AND (SELECT "dwc:acceptedNameUsageID" FROM taxon WHERE id = NEW."dwc:acceptedNameUsageID") IS NOT NULL
BEGIN
  SELECT RAISE(ABORT,
    'acceptedNameUsageID must reference an accepted name, not a synonym (no chained synonyms)');
END;
"""


def _create() -> None:
    op.execute(f'CREATE TRIGGER trg_taxon_synonym_parent_matches_accepted_ins '
               f'BEFORE INSERT ON taxon{_PARENT_MATCH_BODY}')
    op.execute(f'CREATE TRIGGER trg_taxon_synonym_parent_matches_accepted_upd '
               f'BEFORE UPDATE ON taxon{_PARENT_MATCH_BODY}')
    op.execute(f'CREATE TRIGGER trg_taxon_accepted_is_terminal_ins '
               f'BEFORE INSERT ON taxon{_TERMINAL_BODY}')
    op.execute(f'CREATE TRIGGER trg_taxon_accepted_is_terminal_upd '
               f'BEFORE UPDATE ON taxon{_TERMINAL_BODY}')


def upgrade() -> None:
    _create()


def downgrade() -> None:
    for name, _event in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
