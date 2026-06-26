"""geography controlled vocabularies for collecting_event (#40)

country / stateProvince / county / island become single-name controlled vocabularies
(like preparation / habitat), and a new local administrative_region (Regierungsbezirk
tier — no DwC term) is added — all FK on collecting_event (ON DELETE RESTRICT), so the
faceted Explore search has consistent, mergeable values. Existing distinct text values
are migrated into rows and linked, then the four DwC text columns are dropped.
municipality / locality stay free text; country_code (dwc:countryCode) stays a per-event
column. DwC strings resolve from name at export; administrative_region is local-only.

Native ADD COLUMN (with FK) + DROP COLUMN (SQLite ≥ 3.35) so the STRICT collecting_event
table is NOT rebuilt — every other constraint is preserved (CLAUDE.md migration discipline).

Revision ID: 0041
Revises: 0040
"""
from typing import Union

from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels = None
depends_on = None


def _create_vocab_table(name: str) -> None:
    op.execute(f"""
        CREATE TABLE {name} (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        ) STRICT
    """)


def _migrate_column(*, vocab_table: str, fk_col: str, text_col: str) -> None:
    op.execute(
        f'ALTER TABLE collecting_event ADD COLUMN {fk_col} INTEGER '
        f'REFERENCES {vocab_table}(id) ON DELETE RESTRICT'
    )
    op.execute(f"""
        INSERT OR IGNORE INTO {vocab_table} (name, created_at, updated_at)
        SELECT DISTINCT TRIM("{text_col}"),
               strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               strftime('%Y-%m-%dT%H:%M:%fZ','now')
          FROM collecting_event
         WHERE "{text_col}" IS NOT NULL AND TRIM("{text_col}") != ''
    """)
    op.execute(f"""
        UPDATE collecting_event
           SET {fk_col} = (SELECT v.id FROM {vocab_table} v WHERE v.name = TRIM(collecting_event."{text_col}"))
         WHERE "{text_col}" IS NOT NULL AND TRIM("{text_col}") != ''
    """)
    op.execute(f'ALTER TABLE collecting_event DROP COLUMN "{text_col}"')


def upgrade() -> None:
    for t in ("country", "state_province", "county", "island", "administrative_region"):
        _create_vocab_table(t)
    # administrative_region is new (no text column to migrate) — just the FK column.
    op.execute(
        'ALTER TABLE collecting_event ADD COLUMN administrative_region_id INTEGER '
        'REFERENCES administrative_region(id) ON DELETE RESTRICT'
    )
    _migrate_column(vocab_table="country",        fk_col="country_id",        text_col="dwc:country")
    _migrate_column(vocab_table="state_province", fk_col="state_province_id", text_col="dwc:stateProvince")
    _migrate_column(vocab_table="county",         fk_col="county_id",         text_col="dwc:county")
    _migrate_column(vocab_table="island",         fk_col="island_id",         text_col="dwc:island")


def downgrade() -> None:
    def _restore(*, vocab_table, fk_col, text_col):
        op.execute(f'ALTER TABLE collecting_event ADD COLUMN "{text_col}" TEXT')
        op.execute(f"""
            UPDATE collecting_event
               SET "{text_col}" = (SELECT v.name FROM {vocab_table} v WHERE v.id = collecting_event.{fk_col})
             WHERE {fk_col} IS NOT NULL
        """)
        op.execute(f'ALTER TABLE collecting_event DROP COLUMN {fk_col}')

    _restore(vocab_table="country",        fk_col="country_id",        text_col="dwc:country")
    _restore(vocab_table="state_province", fk_col="state_province_id", text_col="dwc:stateProvince")
    _restore(vocab_table="county",         fk_col="county_id",         text_col="dwc:county")
    _restore(vocab_table="island",         fk_col="island_id",         text_col="dwc:island")
    op.execute('ALTER TABLE collecting_event DROP COLUMN administrative_region_id')
    for t in ("administrative_region", "island", "county", "state_province", "country"):
        op.drop_table(t)
