"""person.consent_approved — collector data-sharing consent flag

A local-only flag recording that the person was *asked and agreed* to be named in
the published dataset. Complements `confidential` (migration 0043): confidential
obscures the name on export, consent_approved documents that explicit consent was
obtained to publish it. Informational / curatorial — it does not by itself change
the export (confidential drives obscuring); it is the audit trail behind a decision.

INTEGER 0/1 with a named CHECK, native ADD COLUMN (person is not STRICT, but keep
the same shape as 0043).

Revision ID: 0044
Revises: 0043
"""
from typing import Union

from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two column CHECKs: the 0/1 domain, and mutual exclusion with confidential
    # (a person is either obscured OR named-with-consent, never both). SQLite
    # column CHECKs may reference other columns; existing rows default to 0 so the
    # mutual-exclusion check holds for them.
    op.execute(
        "ALTER TABLE person ADD COLUMN consent_approved INTEGER NOT NULL DEFAULT 0 "
        "CONSTRAINT ck_person_consent_approved CHECK (consent_approved IN (0, 1)) "
        "CONSTRAINT ck_person_consent_xor_confidential "
        "CHECK (consent_approved = 0 OR confidential = 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE person DROP COLUMN consent_approved")
