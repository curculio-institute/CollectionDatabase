"""refine collecting_event: add municipality + locality, drop eventID + recordNumber

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

Changes:
- ADD municipality (DwC Location, between county and locality)
- ADD locality    (DwC Location, standardised description; verbatimLocality is the raw label text)
- DROP eventID    (redundant: derived from internal id at DwC export time)
- DROP recordNumber (DwC Occurrence-class, not Event-class; store on collection_object if needed)
- fieldNumber stays (DwC Event-class: "identifier given to the event in the field")

Batch mode cannot carry CHECK constraints from a STRICT table created with raw SQL, so
we recreate the table explicitly.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS collecting_event_new")
    op.execute("""
    CREATE TABLE collecting_event_new (
        id                              INTEGER PRIMARY KEY,
        verbatimLabel                   TEXT,
        country                         TEXT,
        countryCode                     TEXT CHECK (countryCode IS NULL OR length(countryCode) = 2),
        stateProvince                   TEXT,
        county                          TEXT,
        municipality                    TEXT,
        locality                        TEXT,
        verbatimLocality                TEXT,
        decimalLatitude                 REAL CHECK (decimalLatitude IS NULL OR (decimalLatitude >= -90.0 AND decimalLatitude <= 90.0)),
        decimalLongitude                REAL CHECK (decimalLongitude IS NULL OR (decimalLongitude >= -180.0 AND decimalLongitude <= 180.0)),
        geodeticDatum                   TEXT DEFAULT 'WGS84',
        coordinateUncertaintyInMeters   REAL CHECK (coordinateUncertaintyInMeters IS NULL OR coordinateUncertaintyInMeters >= 0.0),
        minimumElevationInMeters        REAL,
        maximumElevationInMeters        REAL,
        verbatimElevation               TEXT,
        eventDate                       TEXT,
        verbatimEventDate               TEXT,
        fieldNumber                     TEXT,
        habitat                         TEXT,
        samplingProtocol                TEXT,
        recordedBy                      TEXT,
        habitat_enriched                TEXT,
        habitat_ambiguous               INTEGER CHECK (habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)),
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    INSERT INTO collecting_event_new (
        id, verbatimLabel, country, countryCode, stateProvince, county,
        municipality, locality, verbatimLocality,
        decimalLatitude, decimalLongitude, geodeticDatum, coordinateUncertaintyInMeters,
        minimumElevationInMeters, maximumElevationInMeters, verbatimElevation,
        eventDate, verbatimEventDate, fieldNumber,
        habitat, samplingProtocol, recordedBy,
        habitat_enriched, habitat_ambiguous, created_at, updated_at
    )
    SELECT
        id, verbatimLabel, country, countryCode, stateProvince, county,
        NULL, NULL, verbatimLocality,
        decimalLatitude, decimalLongitude, geodeticDatum, coordinateUncertaintyInMeters,
        minimumElevationInMeters, maximumElevationInMeters, verbatimElevation,
        eventDate, verbatimEventDate, fieldNumber,
        habitat, samplingProtocol, recordedBy,
        habitat_enriched, habitat_ambiguous, created_at, updated_at
    FROM collecting_event
    """)

    op.execute("DROP TABLE collecting_event")
    op.execute("ALTER TABLE collecting_event_new RENAME TO collecting_event")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS collecting_event_old")
    op.execute("""
    CREATE TABLE collecting_event_old (
        id                              INTEGER PRIMARY KEY,
        verbatimLabel                   TEXT,
        country                         TEXT,
        countryCode                     TEXT CHECK (countryCode IS NULL OR length(countryCode) = 2),
        stateProvince                   TEXT,
        county                          TEXT,
        verbatimLocality                TEXT,
        decimalLatitude                 REAL CHECK (decimalLatitude IS NULL OR (decimalLatitude >= -90.0 AND decimalLatitude <= 90.0)),
        decimalLongitude                REAL CHECK (decimalLongitude IS NULL OR (decimalLongitude >= -180.0 AND decimalLongitude <= 180.0)),
        geodeticDatum                   TEXT DEFAULT 'WGS84',
        coordinateUncertaintyInMeters   REAL CHECK (coordinateUncertaintyInMeters IS NULL OR coordinateUncertaintyInMeters >= 0.0),
        minimumElevationInMeters        REAL,
        maximumElevationInMeters        REAL,
        verbatimElevation               TEXT,
        eventDate                       TEXT,
        verbatimEventDate               TEXT,
        eventID                         TEXT,
        fieldNumber                     TEXT,
        recordNumber                    TEXT,
        habitat                         TEXT,
        samplingProtocol                TEXT,
        recordedBy                      TEXT,
        habitat_enriched                TEXT,
        habitat_ambiguous               INTEGER CHECK (habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)),
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    INSERT INTO collecting_event_old (
        id, verbatimLabel, country, countryCode, stateProvince, county,
        verbatimLocality,
        decimalLatitude, decimalLongitude, geodeticDatum, coordinateUncertaintyInMeters,
        minimumElevationInMeters, maximumElevationInMeters, verbatimElevation,
        eventDate, verbatimEventDate, eventID, fieldNumber, recordNumber,
        habitat, samplingProtocol, recordedBy,
        habitat_enriched, habitat_ambiguous, created_at, updated_at
    )
    SELECT
        id, verbatimLabel, country, countryCode, stateProvince, county,
        verbatimLocality,
        decimalLatitude, decimalLongitude, geodeticDatum, coordinateUncertaintyInMeters,
        minimumElevationInMeters, maximumElevationInMeters, verbatimElevation,
        eventDate, verbatimEventDate, NULL, fieldNumber, NULL,
        habitat, samplingProtocol, recordedBy,
        habitat_enriched, habitat_ambiguous, created_at, updated_at
    FROM collecting_event
    """)

    op.execute("DROP TABLE collecting_event")
    op.execute("ALTER TABLE collecting_event_old RENAME TO collecting_event")
