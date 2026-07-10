"""drop collecting_event."dwc:countryCode" — it is derived from country.iso_code

Since 0056 the country vocab row carries its ISO 3166-1 code, so the event column is a
second, independent source for the same fact. Nothing tied them together: `country =
Germany` with `countryCode = FR` saved happily. This is the exact failure that killed
`dwc:taxonomicStatus` in migration 0030 — *"storing a derived value let it drift out of
sync ... one row had already drifted"* — and the same rule applies: derive it, never store
it. `dwc:countryCode` is emitted at DwC-export time from `country.iso_code`.

**The code is migrated onto the vocab row before the column is dropped**, so no information
is lost. For each country row that has no code, the distinct non-null `countryCode` of its
events is adopted — but only when the events agree on exactly one code, and only when that
would not collide with an existing (name, code) row. A disagreement is left alone rather
than resolved by guessing which event was right; the code is not required, and the row can
be corrected in Controlled Vocabularies.

`dwc:countryCode` is named by a table-level CHECK (`ck_ce_country_code_len`), and SQLite
cannot drop a column a CHECK mentions — so `collecting_event` must be REBUILT. Per CLAUDE.md
"Migration discipline — never lose constraints", the new DDL re-declares STRICT, all four
remaining named CHECKs, the `dwc:geodeticDatum` server default, and every FK ON DELETE
action verbatim. `tests/test_schema_integrity.py` guards the result.

Revision ID: 0057
Revises: 0056
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every column of the rebuilt table, in order — i.e. the old set minus "dwc:countryCode".
_COLS = '''id, "dwc:verbatimLabel", "dwc:continent", "dwc:municipality", "dwc:locality",
    "dwc:verbatimLocality", "dwc:locationRemarks", "dwc:decimalLatitude",
    "dwc:decimalLongitude", "dwc:geodeticDatum", "dwc:coordinateUncertaintyInMeters",
    "dwc:coordinatePrecision", "dwc:verbatimCoordinates", "dwc:verbatimCoordinateSystem",
    "dwc:minimumElevationInMeters", "dwc:maximumElevationInMeters", "dwc:verbatimElevation",
    "dwc:georeferencedBy", "dwc:georeferencedDate", "dwc:georeferenceProtocol",
    "dwc:georeferenceSources", "dwc:georeferenceRemarks",
    "dwc:georeferenceVerificationStatus", "dwc:eventDate", "dwc:verbatimEventDate",
    "dwc:fieldNumber", recorded_by_id, "dwc:eventRemarks", habitat_enriched,
    habitat_ambiguous, created_at, updated_at, habitat_id, sampling_protocol_id,
    administrative_region_id, country_id, state_province_id, county_id, island_id,
    confidential'''

_NEW_TABLE = f'''CREATE TABLE collecting_event_new (
    id INTEGER NOT NULL,
    "dwc:verbatimLabel" TEXT,
    "dwc:continent" TEXT,
    "dwc:municipality" TEXT,
    "dwc:locality" TEXT,
    "dwc:verbatimLocality" TEXT,
    "dwc:locationRemarks" TEXT,
    "dwc:decimalLatitude" REAL,
    "dwc:decimalLongitude" REAL,
    "dwc:geodeticDatum" TEXT DEFAULT 'WGS84',
    "dwc:coordinateUncertaintyInMeters" REAL,
    "dwc:coordinatePrecision" REAL,
    "dwc:verbatimCoordinates" TEXT,
    "dwc:verbatimCoordinateSystem" TEXT,
    "dwc:minimumElevationInMeters" REAL,
    "dwc:maximumElevationInMeters" REAL,
    "dwc:verbatimElevation" TEXT,
    "dwc:georeferencedBy" TEXT,
    "dwc:georeferencedDate" TEXT,
    "dwc:georeferenceProtocol" TEXT,
    "dwc:georeferenceSources" TEXT,
    "dwc:georeferenceRemarks" TEXT,
    "dwc:georeferenceVerificationStatus" TEXT,
    "dwc:eventDate" TEXT,
    "dwc:verbatimEventDate" TEXT,
    "dwc:fieldNumber" TEXT,
    recorded_by_id INTEGER,
    "dwc:eventRemarks" TEXT,
    habitat_enriched TEXT,
    habitat_ambiguous INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    habitat_id INTEGER REFERENCES habitat(id) ON DELETE RESTRICT,
    sampling_protocol_id INTEGER REFERENCES sampling_protocol(id) ON DELETE RESTRICT,
    administrative_region_id INTEGER REFERENCES administrative_region(id) ON DELETE RESTRICT,
    country_id INTEGER REFERENCES country(id) ON DELETE RESTRICT,
    state_province_id INTEGER REFERENCES state_province(id) ON DELETE RESTRICT,
    county_id INTEGER REFERENCES county(id) ON DELETE RESTRICT,
    island_id INTEGER REFERENCES island(id) ON DELETE RESTRICT,
    confidential INTEGER NOT NULL DEFAULT 0
        CONSTRAINT ck_ce_confidential CHECK (confidential IN (0, 1)),
    PRIMARY KEY (id),
    CONSTRAINT ck_ce_lat_range CHECK ("dwc:decimalLatitude" IS NULL
        OR ("dwc:decimalLatitude" >= -90.0 AND "dwc:decimalLatitude" <= 90.0)),
    CONSTRAINT ck_ce_lon_range CHECK ("dwc:decimalLongitude" IS NULL
        OR ("dwc:decimalLongitude" >= -180.0 AND "dwc:decimalLongitude" <= 180.0)),
    CONSTRAINT ck_ce_uncertainty_positive CHECK ("dwc:coordinateUncertaintyInMeters" IS NULL
        OR "dwc:coordinateUncertaintyInMeters" >= 0.0),
    CONSTRAINT ck_ce_habitat_ambiguous_bool CHECK (habitat_ambiguous IS NULL
        OR habitat_ambiguous IN (0, 1)),
    FOREIGN KEY(recorded_by_id) REFERENCES person (id) ON DELETE RESTRICT
) STRICT'''


def _backfill_country_iso(bind) -> None:
    """Move each event's countryCode onto its country vocab row, before the column dies."""
    rows = bind.exec_driver_sql("""
        SELECT c.id, c.name,
               COUNT(DISTINCT ce."dwc:countryCode") AS n_codes,
               MIN(ce."dwc:countryCode")            AS code
          FROM country c
          JOIN collecting_event ce ON ce.country_id = c.id
         WHERE c.iso_code IS NULL AND ce."dwc:countryCode" IS NOT NULL
         GROUP BY c.id, c.name
    """).fetchall()
    for cid, name, n_codes, code in rows:
        if n_codes != 1:
            # The events disagree about this country's code. Guessing which is right would
            # invent data; leave the row uncoded and let the user fix it in the vocab tab.
            continue
        clash = bind.exec_driver_sql(
            "SELECT 1 FROM country WHERE name = ? AND iso_code = ? AND id <> ?",
            (name, code, cid),
        ).fetchone()
        if clash:
            continue      # a correctly-coded row already exists; merging is the user's call
        bind.exec_driver_sql(
            "UPDATE country SET iso_code = ? WHERE id = ?", (code, cid))


def upgrade() -> None:
    bind = op.get_bind()
    # The pragma MUST be the first statement: `PRAGMA foreign_keys` is a **no-op inside a
    # transaction**, and any DML (the backfill below) opens one. Issue it first, then the
    # backfill and the rebuild both run with enforcement off. collecting_event is referenced
    # by collection_object + media_attachment, so the DROP fails otherwise.
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        _backfill_country_iso(bind)
        op.execute(_NEW_TABLE)
        op.execute(f"INSERT INTO collecting_event_new ({_COLS}) SELECT {_COLS} FROM collecting_event")
        op.execute("DROP TABLE collecting_event")
        op.execute("ALTER TABLE collecting_event_new RENAME TO collecting_event")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    # Re-adds the column (empty) and its CHECK. The per-event values are not recoverable —
    # they were folded into country.iso_code, which is where they now belong.
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        old = _NEW_TABLE.replace(
            '"dwc:continent" TEXT,',
            '"dwc:continent" TEXT,\n    "dwc:countryCode" TEXT,'
        ).replace(
            "CONSTRAINT ck_ce_habitat_ambiguous_bool",
            'CONSTRAINT ck_ce_country_code_len CHECK ("dwc:countryCode" IS NULL'
            ' OR length("dwc:countryCode") = 2),\n'
            "    CONSTRAINT ck_ce_habitat_ambiguous_bool"
        ).replace("collecting_event_new", "collecting_event_old")
        op.execute(old)
        op.execute(f"INSERT INTO collecting_event_old ({_COLS}) SELECT {_COLS} FROM collecting_event")
        op.execute("DROP TABLE collecting_event")
        op.execute("ALTER TABLE collecting_event_old RENAME TO collecting_event")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")
