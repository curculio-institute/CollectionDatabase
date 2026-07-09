"""taxon."dwc:nomenclaturalCode" NOT NULL + CHECK — every name is governed by a code (#96)

Issue #96: plants imported from POWO landed with no nomenclatural code, their ancestor rows
too, and code-less taxa surfaced under the Taxonomy tab's ICZN filter. The *cause* was a
swallowed Cloudflare 403 (fixed by replacing POWO with WCVP, #98). This migration is the
structural backstop that makes a code-less row impossible regardless of source.

The code is never guessed: it is a property of the source (WCVP indexes only ICN-governed
names; TaxonWorks reports its own) or inherited from the parent. So a NULL cannot be filled
in — it can only mean an importer failed to supply one. Accordingly this migration REFUSES to
run if any NULL exists rather than defaulting one in (CLAUDE.md: no fallback defaults).

SQLite cannot add NOT NULL to an existing column, so `taxon` is rebuilt. Per the DB-1 rule a
rebuild must re-declare EVERYTHING the table had, because a reflected `batch_alter_table`
silently drops STRICT, CHECKs, UNIQUEs and FK ON DELETE actions. Verified present on `taxon`
before this migration and re-declared below:

  * STRICT
  * FK "dwc:parentNameUsageID"   → taxon(id) ON DELETE RESTRICT
  * FK "dwc:acceptedNameUsageID" → taxon(id) ON DELETE RESTRICT
  * index ix_taxon_parent_name_usage_id
  * triggers trg_taxon_accepted_is_terminal_ins AND _upd  (there are TWO; a rebuild that
    restores one is precisely the 0021/0024 regression)

Note what is deliberately NOT re-created: trg_taxon_synonym_parent_matches_accepted_{ins,upd},
retired by migration 0033 when the model moved to own-lineage parenting (Epic #30). Do not
re-introduce them.

The CHECK pins the closed standard vocabulary (app/vocab.py::NOMENCLATURAL_CODES). These are
the codes themselves, not user-coined terms, so they are a fixed list, never an editable vocab.

Revision ID: 0054
Revises: 0053
"""
from typing import Union

from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels = None
depends_on = None

_TERMINAL_BODY = """
WHEN NEW."dwc:acceptedNameUsageID" IS NOT NULL
 AND (SELECT "dwc:acceptedNameUsageID" FROM taxon WHERE id = NEW."dwc:acceptedNameUsageID") IS NOT NULL
BEGIN
  SELECT RAISE(ABORT,
    'acceptedNameUsageID must reference an accepted name, not a synonym (no chained synonyms)');
END;
"""


def upgrade() -> None:
    bind = op.get_bind()

    orphans = bind.exec_driver_sql(
        'SELECT count(*) FROM taxon WHERE "dwc:nomenclaturalCode" IS NULL'
    ).scalar()
    if orphans:
        rows = bind.exec_driver_sql(
            'SELECT id, "dwc:scientificName", "dwc:taxonRank" FROM taxon '
            'WHERE "dwc:nomenclaturalCode" IS NULL LIMIT 20'
        ).fetchall()
        raise RuntimeError(
            f"{orphans} taxon row(s) have no nomenclaturalCode, e.g. {list(rows)}. "
            "The code is a property of the source or inherited from the parent; it cannot "
            "be guessed here. Set it (Taxonomy tab → edit taxon, or by hand) and re-run."
        )

    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute("""CREATE TABLE taxon_new (
            id INTEGER NOT NULL,
            "dwc:scientificName" TEXT NOT NULL,
            "dwc:taxonRank" TEXT NOT NULL,
            "dwc:scientificNameAuthorship" TEXT,
            "dwc:parentNameUsageID" INTEGER,
            "dwc:acceptedNameUsageID" INTEGER,
            "taxonworksOtuID" INTEGER,
            "dwc:nomenclaturalCode" TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            name_element TEXT,
            sort_order INTEGER,
            "ipniID" TEXT,
            PRIMARY KEY (id),
            CONSTRAINT ck_taxon_nomenclatural_code CHECK (
                "dwc:nomenclaturalCode" IN ('ICZN', 'ICN', 'ICNP', 'ICVCN')),
            FOREIGN KEY("dwc:parentNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT,
            FOREIGN KEY("dwc:acceptedNameUsageID") REFERENCES taxon (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute('''INSERT INTO taxon_new (
            id, "dwc:scientificName", "dwc:taxonRank", "dwc:scientificNameAuthorship",
            "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID",
            "dwc:nomenclaturalCode", created_at, updated_at, name_element, sort_order, "ipniID")
            SELECT id, "dwc:scientificName", "dwc:taxonRank", "dwc:scientificNameAuthorship",
            "dwc:parentNameUsageID", "dwc:acceptedNameUsageID", "taxonworksOtuID",
            "dwc:nomenclaturalCode", created_at, updated_at, name_element, sort_order, "ipniID"
            FROM taxon''')
        op.execute("DROP TABLE taxon")
        op.execute("ALTER TABLE taxon_new RENAME TO taxon")

        op.execute('CREATE INDEX ix_taxon_parent_name_usage_id '
                   'ON taxon ("dwc:parentNameUsageID")')
        op.execute(f'CREATE TRIGGER trg_taxon_accepted_is_terminal_ins '
                   f'BEFORE INSERT ON taxon{_TERMINAL_BODY}')
        op.execute(f'CREATE TRIGGER trg_taxon_accepted_is_terminal_upd '
                   f'BEFORE UPDATE ON taxon{_TERMINAL_BODY}')

        rows = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
        if rows:
            raise RuntimeError(f"FK check failed after taxon rebuild: {rows}")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    # Intentional no-op: reversing would re-admit code-less taxa, which is the bug (#96).
    pass
