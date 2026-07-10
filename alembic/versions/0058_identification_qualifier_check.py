"""taxon_determination.identificationQualifier — a closed CHECK-constrained set

dwc:identificationQualifier is a closed standard vocabulary (CLAUDE.md §4: "Closed standard
vocabularies stay fixed CHECK-constrained lists"), but the column was unconstrained free text.
Each qualifier carries a specific taxonomic meaning (cf. = tentative, aff. = has affinity,
sp. = undetermined, …), so an off-list value is a data error, not a variant to tolerate.

Adds ``ck_td_identification_qualifier``: the value is NULL (a definite identification) or one
of the open-nomenclature set in ``app/vocab.py::IDENTIFICATION_QUALIFIERS``.

SQLite cannot ALTER ... ADD CONSTRAINT, so taxon_determination is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints"), the new DDL re-declares
STRICT, the existing ck_td_is_current_bool CHECK, the is_current server default, and all three
FK ON DELETE actions verbatim; the three indexes are recreated after the rename. The
INSERT...SELECT enforces the new CHECK on existing rows (safe: the only stored qualifier is
NULL). tests/test_schema_integrity.py guards the result.

Revision ID: 0058
Revises: 0057
"""
from typing import Sequence, Union

from alembic import op

from app.vocab import IDENTIFICATION_QUALIFIERS

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_QUAL_LIST = ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS)
_QUAL_CHECK = (
    'CONSTRAINT ck_td_identification_qualifier CHECK ('
    '"dwc:identificationQualifier" IS NULL '
    f'OR "dwc:identificationQualifier" IN ({_QUAL_LIST}))'
)

_COLS = ('id, collection_object_id, taxon_id, "dwc:verbatimIdentification", "dwc:sex", '
         '"dwc:typeStatus", identified_by_id, "dwc:dateIdentified", '
         '"dwc:identificationQualifier", "dwc:identificationRemarks", is_current, '
         'created_at, updated_at')


def _new_table(name: str, *, with_qual_check: bool) -> str:
    qual = f",\n    {_QUAL_CHECK}" if with_qual_check else ""
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    collection_object_id INTEGER NOT NULL,
    taxon_id INTEGER NOT NULL,
    "dwc:verbatimIdentification" TEXT,
    "dwc:sex" TEXT,
    "dwc:typeStatus" TEXT,
    identified_by_id INTEGER,
    "dwc:dateIdentified" TEXT,
    "dwc:identificationQualifier" TEXT,
    "dwc:identificationRemarks" TEXT,
    is_current INTEGER DEFAULT 1 NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_td_is_current_bool CHECK (is_current IN (0, 1)){qual},
    FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE,
    FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT,
    FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT
) STRICT'''


def _indexes(table: str) -> list[str]:
    return [
        f"CREATE INDEX ix_td_co_id ON {table} (collection_object_id)",
        f"CREATE INDEX ix_td_taxon_id ON {table} (taxon_id)",
        f"CREATE UNIQUE INDEX uq_td_one_current_per_co ON {table} "
        "(collection_object_id) WHERE is_current = 1",
    ]


def _rebuild(*, with_qual_check: bool) -> None:
    bind = op.get_bind()
    # PRAGMA first — it is a no-op inside a transaction, and the INSERT opens one; the DROP
    # then fails on the FKs otherwise (learned in 0057).
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("taxon_determination_new", with_qual_check=with_qual_check))
        op.execute(f"INSERT INTO taxon_determination_new ({_COLS}) "
                   f"SELECT {_COLS} FROM taxon_determination")
        op.execute("DROP TABLE taxon_determination")
        op.execute("ALTER TABLE taxon_determination_new RENAME TO taxon_determination")
        for stmt in _indexes("taxon_determination"):
            op.execute(stmt)
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_qual_check=True)


def downgrade() -> None:
    _rebuild(with_qual_check=False)
