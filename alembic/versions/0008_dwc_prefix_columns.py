"""Prefix all DwC term column names with dwc:

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-04

Column names that are Darwin Core terms are renamed to their fully-qualified
dwc: form (e.g. identifiedBy → dwc:identifiedBy). This makes the DB schema
self-documenting and turns DwC-A CSV export into a near-direct projection.

Local columns (id, FKs, created_at/updated_at, catalogNamespace, taxonworksID,
is_current, habitat_enriched, habitat_ambiguous) are unchanged — they have no
DwC equivalent.

Four tables rebuilt: taxon, collecting_event, collection_object, taxon_determination.
biological_relationship and biological_association have no DwC columns.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── taxon ──────────────────────────────────────────────────────────────────

_TAXON_NEW = """
    CREATE TABLE taxon_new (
        id                              INTEGER PRIMARY KEY,
        "dwc:scientificName"            TEXT NOT NULL,
        "dwc:scientificNameAuthorship"  TEXT,
        "dwc:taxonRank"                 TEXT,
        "dwc:specificEpithet"           TEXT,
        "dwc:infraspecificEpithet"      TEXT,
        "dwc:taxonomicStatus"           TEXT,
        "dwc:taxonRemarks"              TEXT,
        parent_id                       INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
        "dwc:kingdom"                   TEXT,
        "dwc:phylum"                    TEXT,
        "dwc:class"                     TEXT,
        "dwc:order"                     TEXT,
        "dwc:family"                    TEXT,
        "dwc:subfamily"                 TEXT,
        "dwc:tribe"                     TEXT,
        "dwc:subtribe"                  TEXT,
        "dwc:genus"                     TEXT,
        "dwc:subgenus"                  TEXT,
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL
    ) STRICT"""

_TAXON_COPY = """
    INSERT INTO taxon_new (
        id, "dwc:scientificName", "dwc:scientificNameAuthorship", "dwc:taxonRank",
        "dwc:specificEpithet", "dwc:infraspecificEpithet", "dwc:taxonomicStatus",
        "dwc:taxonRemarks", parent_id,
        "dwc:kingdom", "dwc:phylum", "dwc:class", "dwc:order",
        "dwc:family", "dwc:subfamily", "dwc:tribe", "dwc:subtribe",
        "dwc:genus", "dwc:subgenus", created_at, updated_at
    )
    SELECT
        id, scientificName, scientificNameAuthorship, taxonRank,
        specificEpithet, infraspecificEpithet, taxonomicStatus,
        taxonRemarks, parent_id,
        kingdom, phylum, "class", "order",
        family, subfamily, tribe, subtribe,
        genus, subgenus, created_at, updated_at
    FROM taxon"""


# ── collecting_event ────────────────────────────────────────────────────────

_CE_NEW = """
    CREATE TABLE collecting_event_new (
        id                                  INTEGER PRIMARY KEY,
        "dwc:verbatimLabel"                 TEXT,
        "dwc:continent"                     TEXT,
        "dwc:country"                       TEXT,
        "dwc:countryCode"                   TEXT CHECK ("dwc:countryCode" IS NULL OR length("dwc:countryCode") = 2),
        "dwc:stateProvince"                 TEXT,
        "dwc:county"                        TEXT,
        "dwc:municipality"                  TEXT,
        "dwc:locality"                      TEXT,
        "dwc:verbatimLocality"              TEXT,
        "dwc:locationRemarks"               TEXT,
        "dwc:decimalLatitude"               REAL CHECK ("dwc:decimalLatitude" IS NULL OR ("dwc:decimalLatitude" >= -90.0 AND "dwc:decimalLatitude" <= 90.0)),
        "dwc:decimalLongitude"              REAL CHECK ("dwc:decimalLongitude" IS NULL OR ("dwc:decimalLongitude" >= -180.0 AND "dwc:decimalLongitude" <= 180.0)),
        "dwc:geodeticDatum"                 TEXT DEFAULT 'WGS84',
        "dwc:coordinateUncertaintyInMeters" REAL CHECK ("dwc:coordinateUncertaintyInMeters" IS NULL OR "dwc:coordinateUncertaintyInMeters" >= 0.0),
        "dwc:coordinatePrecision"           REAL,
        "dwc:verbatimCoordinates"           TEXT,
        "dwc:verbatimCoordinateSystem"      TEXT,
        "dwc:minimumElevationInMeters"      REAL,
        "dwc:maximumElevationInMeters"      REAL,
        "dwc:verbatimElevation"             TEXT,
        "dwc:georeferencedBy"              TEXT,
        "dwc:georeferencedDate"            TEXT,
        "dwc:georeferenceProtocol"         TEXT,
        "dwc:georeferenceSources"          TEXT,
        "dwc:georeferenceRemarks"          TEXT,
        "dwc:georeferenceVerificationStatus" TEXT,
        "dwc:eventDate"                     TEXT,
        "dwc:verbatimEventDate"             TEXT,
        "dwc:fieldNumber"                   TEXT,
        "dwc:habitat"                       TEXT,
        "dwc:samplingProtocol"              TEXT,
        "dwc:recordedBy"                    TEXT,
        "dwc:eventRemarks"                  TEXT,
        habitat_enriched                    TEXT,
        habitat_ambiguous                   INTEGER CHECK (habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)),
        created_at                          TEXT NOT NULL,
        updated_at                          TEXT NOT NULL
    ) STRICT"""

_CE_COLS_NEW = """
    "dwc:verbatimLabel", "dwc:continent", "dwc:country", "dwc:countryCode",
    "dwc:stateProvince", "dwc:county", "dwc:municipality", "dwc:locality",
    "dwc:verbatimLocality", "dwc:locationRemarks",
    "dwc:decimalLatitude", "dwc:decimalLongitude", "dwc:geodeticDatum",
    "dwc:coordinateUncertaintyInMeters", "dwc:coordinatePrecision",
    "dwc:verbatimCoordinates", "dwc:verbatimCoordinateSystem",
    "dwc:minimumElevationInMeters", "dwc:maximumElevationInMeters", "dwc:verbatimElevation",
    "dwc:georeferencedBy", "dwc:georeferencedDate", "dwc:georeferenceProtocol",
    "dwc:georeferenceSources", "dwc:georeferenceRemarks", "dwc:georeferenceVerificationStatus",
    "dwc:eventDate", "dwc:verbatimEventDate", "dwc:fieldNumber",
    "dwc:habitat", "dwc:samplingProtocol", "dwc:recordedBy", "dwc:eventRemarks"
"""

_CE_COLS_OLD = """
    verbatimLabel, continent, country, countryCode,
    stateProvince, county, municipality, locality,
    verbatimLocality, locationRemarks,
    decimalLatitude, decimalLongitude, geodeticDatum,
    coordinateUncertaintyInMeters, coordinatePrecision,
    verbatimCoordinates, verbatimCoordinateSystem,
    minimumElevationInMeters, maximumElevationInMeters, verbatimElevation,
    georeferencedBy, georeferencedDate, georeferenceProtocol,
    georeferenceSources, georeferenceRemarks, georeferenceVerificationStatus,
    eventDate, verbatimEventDate, fieldNumber,
    habitat, samplingProtocol, recordedBy, eventRemarks
"""


# ── collection_object ───────────────────────────────────────────────────────

_CO_NEW = """
    CREATE TABLE collection_object_new (
        id                      INTEGER PRIMARY KEY,
        collecting_event_id     INTEGER REFERENCES collecting_event(id) ON DELETE RESTRICT,
        "dwc:catalogNumber"     TEXT NOT NULL,
        catalogNamespace        TEXT NOT NULL,
        "dwc:basisOfRecord"     TEXT NOT NULL DEFAULT 'PreservedSpecimen',
        "dwc:individualCount"   INTEGER NOT NULL DEFAULT 1 CHECK ("dwc:individualCount" >= 0),
        "dwc:lifeStage"         TEXT,
        "dwc:sex"               TEXT,
        "dwc:disposition"       TEXT,
        "dwc:ownerInstitutionCode" TEXT,
        "dwc:preparations"      TEXT,
        "dwc:typeStatus"        TEXT,
        "dwc:occurrenceRemarks" TEXT,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        UNIQUE (catalogNamespace, "dwc:catalogNumber")
    ) STRICT"""

_CO_COLS_NEW = """
    "dwc:catalogNumber", catalogNamespace, "dwc:basisOfRecord", "dwc:individualCount",
    "dwc:lifeStage", "dwc:sex", "dwc:disposition", "dwc:ownerInstitutionCode",
    "dwc:preparations", "dwc:typeStatus", "dwc:occurrenceRemarks"
"""

_CO_COLS_OLD = """
    catalogNumber, catalogNamespace, basisOfRecord, individualCount,
    lifeStage, sex, disposition, ownerInstitutionCode,
    preparations, typeStatus, occurrenceRemarks
"""


# ── taxon_determination ─────────────────────────────────────────────────────

_TD_NEW = """
    CREATE TABLE taxon_determination_new (
        id                                      INTEGER PRIMARY KEY,
        collection_object_id                    INTEGER NOT NULL REFERENCES collection_object(id) ON DELETE CASCADE,
        taxon_id                                INTEGER NOT NULL REFERENCES taxon(id) ON DELETE RESTRICT,
        "dwc:verbatimIdentification"            TEXT,
        "dwc:identifiedBy"                      TEXT,
        "dwc:dateIdentified"                    TEXT,
        "dwc:identificationQualifier"           TEXT,
        "dwc:identificationRemarks"             TEXT,
        "dwc:identificationReferences"          TEXT,
        "dwc:identificationVerificationStatus"  TEXT,
        is_current                              INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
        created_at                              TEXT NOT NULL,
        updated_at                              TEXT NOT NULL
    ) STRICT"""

_TD_COLS_NEW = """
    "dwc:verbatimIdentification", "dwc:identifiedBy", "dwc:dateIdentified",
    "dwc:identificationQualifier", "dwc:identificationRemarks",
    "dwc:identificationReferences", "dwc:identificationVerificationStatus"
"""

_TD_COLS_OLD = """
    verbatimIdentification, identifiedBy, dateIdentified,
    identificationQualifier, identificationRemarks,
    identificationReferences, identificationVerificationStatus
"""


def upgrade() -> None:
    # taxon
    op.execute("DROP TABLE IF EXISTS taxon_new")
    op.execute(_TAXON_NEW)
    op.execute(_TAXON_COPY)
    op.execute("DROP TABLE taxon")
    op.execute("ALTER TABLE taxon_new RENAME TO taxon")
    op.execute("CREATE INDEX ix_taxon_parent_id ON taxon (parent_id)")

    # collecting_event
    op.execute("DROP TABLE IF EXISTS collecting_event_new")
    op.execute(_CE_NEW)
    op.execute(f"""
        INSERT INTO collecting_event_new (
            id, {_CE_COLS_NEW}, habitat_enriched, habitat_ambiguous, created_at, updated_at
        )
        SELECT id, {_CE_COLS_OLD}, habitat_enriched, habitat_ambiguous, created_at, updated_at
        FROM collecting_event
    """)
    op.execute("DROP TABLE collecting_event")
    op.execute("ALTER TABLE collecting_event_new RENAME TO collecting_event")

    # collection_object
    op.execute("DROP TABLE IF EXISTS collection_object_new")
    op.execute(_CO_NEW)
    op.execute(f"""
        INSERT INTO collection_object_new (
            id, collecting_event_id, {_CO_COLS_NEW}, created_at, updated_at
        )
        SELECT id, collecting_event_id, {_CO_COLS_OLD}, created_at, updated_at
        FROM collection_object
    """)
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_new RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")

    # taxon_determination
    op.execute("DROP TABLE IF EXISTS taxon_determination_new")
    op.execute(_TD_NEW)
    op.execute(f"""
        INSERT INTO taxon_determination_new (
            id, collection_object_id, taxon_id, {_TD_COLS_NEW}, is_current, created_at, updated_at
        )
        SELECT id, collection_object_id, taxon_id, {_TD_COLS_OLD}, is_current, created_at, updated_at
        FROM taxon_determination
    """)
    op.execute("DROP TABLE taxon_determination")
    op.execute("ALTER TABLE taxon_determination_new RENAME TO taxon_determination")
    op.execute("CREATE INDEX ix_td_co_id ON taxon_determination (collection_object_id)")
    op.execute("CREATE INDEX ix_td_taxon_id ON taxon_determination (taxon_id)")


def downgrade() -> None:
    # taxon_determination
    op.execute("DROP TABLE IF EXISTS taxon_determination_old")
    op.execute(_TD_NEW.replace("taxon_determination_new", "taxon_determination_old")
               .replace('"dwc:verbatimIdentification"', "verbatimIdentification")
               .replace('"dwc:identifiedBy"', "identifiedBy")
               .replace('"dwc:dateIdentified"', "dateIdentified")
               .replace('"dwc:identificationQualifier"', "identificationQualifier")
               .replace('"dwc:identificationRemarks"', "identificationRemarks")
               .replace('"dwc:identificationReferences"', "identificationReferences")
               .replace('"dwc:identificationVerificationStatus"', "identificationVerificationStatus"))
    op.execute(f"""
        INSERT INTO taxon_determination_old (
            id, collection_object_id, taxon_id, {_TD_COLS_OLD}, is_current, created_at, updated_at
        )
        SELECT id, collection_object_id, taxon_id, {_TD_COLS_NEW}, is_current, created_at, updated_at
        FROM taxon_determination
    """)
    op.execute("DROP TABLE taxon_determination")
    op.execute("ALTER TABLE taxon_determination_old RENAME TO taxon_determination")
    op.execute("CREATE INDEX ix_td_co_id ON taxon_determination (collection_object_id)")
    op.execute("CREATE INDEX ix_td_taxon_id ON taxon_determination (taxon_id)")

    # collection_object
    op.execute("DROP TABLE IF EXISTS collection_object_old")
    op.execute("""
        CREATE TABLE collection_object_old (
            id                      INTEGER PRIMARY KEY,
            collecting_event_id     INTEGER REFERENCES collecting_event(id) ON DELETE RESTRICT,
            catalogNumber           TEXT NOT NULL,
            catalogNamespace        TEXT NOT NULL,
            basisOfRecord           TEXT NOT NULL DEFAULT 'PreservedSpecimen',
            individualCount         INTEGER NOT NULL DEFAULT 1 CHECK (individualCount >= 0),
            lifeStage               TEXT,
            sex                     TEXT,
            disposition             TEXT,
            ownerInstitutionCode    TEXT,
            preparations            TEXT,
            typeStatus              TEXT,
            occurrenceRemarks       TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            UNIQUE (catalogNamespace, catalogNumber)
        ) STRICT
    """)
    op.execute(f"""
        INSERT INTO collection_object_old (
            id, collecting_event_id, {_CO_COLS_OLD}, created_at, updated_at
        )
        SELECT id, collecting_event_id, {_CO_COLS_NEW}, created_at, updated_at
        FROM collection_object
    """)
    op.execute("DROP TABLE collection_object")
    op.execute("ALTER TABLE collection_object_old RENAME TO collection_object")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")

    # collecting_event
    op.execute("DROP TABLE IF EXISTS collecting_event_old")
    op.execute(_CE_NEW.replace("collecting_event_new", "collecting_event_old")
               .replace('"dwc:', "").replace('"', ""))
    op.execute(f"""
        INSERT INTO collecting_event_old (
            id, {_CE_COLS_OLD}, habitat_enriched, habitat_ambiguous, created_at, updated_at
        )
        SELECT id, {_CE_COLS_NEW}, habitat_enriched, habitat_ambiguous, created_at, updated_at
        FROM collecting_event
    """)
    op.execute("DROP TABLE collecting_event")
    op.execute("ALTER TABLE collecting_event_old RENAME TO collecting_event")

    # taxon
    op.execute("DROP TABLE IF EXISTS taxon_old")
    op.execute("""
        CREATE TABLE taxon_old (
            id                          INTEGER PRIMARY KEY,
            scientificName              TEXT NOT NULL,
            scientificNameAuthorship    TEXT,
            taxonRank                   TEXT,
            specificEpithet             TEXT,
            infraspecificEpithet        TEXT,
            taxonomicStatus             TEXT,
            taxonRemarks                TEXT,
            parent_id                   INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
            kingdom TEXT, phylum TEXT, "class" TEXT, "order" TEXT,
            family TEXT, subfamily TEXT, tribe TEXT, subtribe TEXT,
            genus TEXT, subgenus TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        ) STRICT
    """)
    op.execute("""
        INSERT INTO taxon_old (
            id, scientificName, scientificNameAuthorship, taxonRank,
            specificEpithet, infraspecificEpithet, taxonomicStatus, taxonRemarks, parent_id,
            kingdom, phylum, "class", "order", family, subfamily, tribe, subtribe,
            genus, subgenus, created_at, updated_at
        )
        SELECT
            id, "dwc:scientificName", "dwc:scientificNameAuthorship", "dwc:taxonRank",
            "dwc:specificEpithet", "dwc:infraspecificEpithet", "dwc:taxonomicStatus",
            "dwc:taxonRemarks", parent_id,
            "dwc:kingdom", "dwc:phylum", "dwc:class", "dwc:order",
            "dwc:family", "dwc:subfamily", "dwc:tribe", "dwc:subtribe",
            "dwc:genus", "dwc:subgenus", created_at, updated_at
        FROM taxon
    """)
    op.execute("DROP TABLE taxon")
    op.execute("ALTER TABLE taxon_old RENAME TO taxon")
    op.execute("CREATE INDEX ix_taxon_parent_id ON taxon (parent_id)")
