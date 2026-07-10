"""country / state_province: identity is (name, iso_code), not name alone

A subdivision NAME does not identify a subdivision. Of the 5,420 ISO 3166-2 subdivisions,
**40 names are shared across different countries** (checked against Wikidata P300):

    Limburg          BE-VLI, NL-LI
    Punjab           IN-PB,  PK-PB
    Amazonas         BR-AM,  VE-Z
    Central Province KE-200, LK-2, MV-CE, PG-CPM, SB-CE, ZM-02

With `UNIQUE(name)` on `state_province`, a specimen from Dutch Limburg either silently
reused the Belgian row or (with the fill-once ISO stamp) refused to save at all. Both are
wrong: the first is a silent wrong value, the second blocks legitimate data.

New rule, applied identically to `country` and `state_province`:

    exact match on (name, iso_code) -> reuse;  otherwise -> create a new row.

No row is ever mutated to carry a code it did not have, and no save is ever refused.
Duplicates that turn out to denote the same place (`Deutschland` / `Germany`, both DE) are
folded afterwards with the existing Vocabulary merge tool — which is exactly what it is for.

`UNIQUE(name)` is replaced by a unique index on `(name, IFNULL(iso_code, ''))`. The IFNULL
is load-bearing: SQLite treats NULL != NULL, so a plain `UNIQUE(name, iso_code)` would let
every hand-typed save create yet another uncoded `Limburg`. With IFNULL there is exactly one
uncoded row per name, plus one row per distinct ISO code.

`country.iso_code` holds ISO 3166-1 alpha-2 (`DE`); `state_province.iso_code` holds ISO
3166-2 (`DE-BY`). `dwc:countryCode` stays a per-event column — it is a Darwin Core term the
export must emit, and it is not the same thing as the vocab row's identity.

Existing rows keep `iso_code = NULL` and stay valid; the code is not required. They are not
back-filled: 40 names are ambiguous, so a name->code backfill would be a guess.

`UNIQUE(name)` is an inline table constraint on a STRICT table, so both tables must be
REBUILT (SQLite cannot drop an inline constraint in place). Per CLAUDE.md "Migration
discipline — never lose constraints", the new DDL re-declares STRICT verbatim; the copy is
INSERT...SELECT so any pre-existing duplicate aborts the migration loudly.
`tests/test_schema_integrity.py` guards the result.

Revision ID: 0056
Revises: 0055
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # The tables are referenced by collecting_event.{country_id,state_province_id}
    # (ON DELETE RESTRICT). Drop+rename needs FK enforcement off, as in migration 0029.
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        # ---- country: gains iso_code (ISO 3166-1 alpha-2) ----
        op.execute("""CREATE TABLE country_new (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            iso_code   TEXT
        ) STRICT""")
        op.execute("""INSERT INTO country_new (id, name, created_at, updated_at, iso_code)
                      SELECT id, name, created_at, updated_at, NULL FROM country""")
        op.execute("DROP TABLE country")
        op.execute("ALTER TABLE country_new RENAME TO country")
        op.execute("CREATE UNIQUE INDEX uq_country_name_iso "
                   "ON country (name, IFNULL(iso_code, ''))")

        # ---- state_province: keeps the iso_code added in 0055 ----
        op.execute("""CREATE TABLE state_province_new (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            iso_code   TEXT
        ) STRICT""")
        op.execute("""INSERT INTO state_province_new (id, name, created_at, updated_at, iso_code)
                      SELECT id, name, created_at, updated_at, iso_code FROM state_province""")
        op.execute("DROP TABLE state_province")
        op.execute("ALTER TABLE state_province_new RENAME TO state_province")
        op.execute("CREATE UNIQUE INDEX uq_state_province_name_iso "
                   "ON state_province (name, IFNULL(iso_code, ''))")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        # Back to UNIQUE(name): fails loudly if two rows now share a name (they cannot be
        # collapsed without deciding which one the events belong to).
        op.execute("DROP INDEX uq_state_province_name_iso")
        op.execute("""CREATE TABLE state_province_old (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            iso_code   TEXT,
            UNIQUE(name)
        ) STRICT""")
        op.execute("""INSERT INTO state_province_old SELECT id, name, created_at, updated_at, iso_code
                      FROM state_province""")
        op.execute("DROP TABLE state_province")
        op.execute("ALTER TABLE state_province_old RENAME TO state_province")

        op.execute("DROP INDEX uq_country_name_iso")
        op.execute("""CREATE TABLE country_old (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        ) STRICT""")
        op.execute("""INSERT INTO country_old SELECT id, name, created_at, updated_at FROM country""")
        op.execute("DROP TABLE country")
        op.execute("ALTER TABLE country_old RENAME TO country")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")
