"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-04
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All tables use SQLite STRICT mode: only INT/INTEGER/REAL/TEXT/BLOB/ANY accepted.
    # Reserved-word column names ('class', 'order') are double-quoted throughout.

    op.execute("""
    CREATE TABLE taxon (
        id                          INTEGER PRIMARY KEY,
        scientificName              TEXT NOT NULL,
        scientificNameAuthorship    TEXT,
        taxonRank                   TEXT,
        parent_id                   INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
        kingdom                     TEXT,
        phylum                      TEXT,
        "class"                     TEXT,
        "order"                     TEXT,
        family                      TEXT,
        subfamily                   TEXT,
        tribe                       TEXT,
        subtribe                    TEXT,
        genus                       TEXT,
        subgenus                    TEXT,
        created_at                  TEXT NOT NULL,
        updated_at                  TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE collecting_event (
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
        habitat                         TEXT,
        samplingProtocol                TEXT,
        recordedBy                      TEXT,
        recordNumber                    TEXT,
        fieldNumber                     TEXT,
        habitat_enriched                TEXT,
        habitat_ambiguous               INTEGER CHECK (habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)),
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE collection_object (
        id                  INTEGER PRIMARY KEY,
        collecting_event_id INTEGER REFERENCES collecting_event(id) ON DELETE RESTRICT,
        basisOfRecord       TEXT NOT NULL DEFAULT 'PreservedSpecimen',
        individualCount     INTEGER NOT NULL DEFAULT 1 CHECK (individualCount >= 0),
        preparations        TEXT,
        sex                 TEXT,
        typeStatus          TEXT,
        occurrenceRemarks   TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE identifier (
        id                    INTEGER PRIMARY KEY,
        collection_object_id  INTEGER NOT NULL REFERENCES collection_object(id) ON DELETE CASCADE,
        namespace             TEXT NOT NULL,
        identifier            TEXT NOT NULL,
        identifier_type       TEXT NOT NULL,
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL,
        UNIQUE (namespace, identifier)
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE taxon_determination (
        id                       INTEGER PRIMARY KEY,
        collection_object_id     INTEGER NOT NULL REFERENCES collection_object(id) ON DELETE CASCADE,
        taxon_id                 INTEGER NOT NULL REFERENCES taxon(id) ON DELETE RESTRICT,
        identifiedBy             TEXT,
        dateIdentified           TEXT,
        identificationQualifier  TEXT,
        identificationRemarks    TEXT,
        is_current               INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
        created_at               TEXT NOT NULL,
        updated_at               TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE biological_relationship (
        id              INTEGER PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        inverted_name   TEXT,
        definition      TEXT,
        is_transitive   INTEGER NOT NULL DEFAULT 0 CHECK (is_transitive IN (0, 1)),
        is_reflexive    INTEGER NOT NULL DEFAULT 0 CHECK (is_reflexive IN (0, 1)),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    ) STRICT
    """)

    op.execute("""
    CREATE TABLE biological_association (
        id                              INTEGER PRIMARY KEY,
        biological_relationship_id      INTEGER NOT NULL REFERENCES biological_relationship(id) ON DELETE RESTRICT,
        subject_collection_object_id    INTEGER REFERENCES collection_object(id) ON DELETE RESTRICT,
        subject_taxon_id                INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
        object_collection_object_id     INTEGER REFERENCES collection_object(id) ON DELETE RESTRICT,
        object_taxon_id                 INTEGER REFERENCES taxon(id) ON DELETE RESTRICT,
        notes                           TEXT,
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL,
        CHECK (
            (subject_collection_object_id IS NOT NULL AND subject_taxon_id IS NULL)
            OR (subject_collection_object_id IS NULL AND subject_taxon_id IS NOT NULL)
        ),
        CHECK (
            (object_collection_object_id IS NOT NULL AND object_taxon_id IS NULL)
            OR (object_collection_object_id IS NULL AND object_taxon_id IS NOT NULL)
        )
    ) STRICT
    """)

    # Indexes on FK columns (SQLite does not auto-index FKs)
    op.execute("CREATE INDEX ix_taxon_parent_id ON taxon (parent_id)")
    op.execute("CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)")
    op.execute("CREATE INDEX ix_identifier_co_id ON identifier (collection_object_id)")
    op.execute("CREATE INDEX ix_td_co_id ON taxon_determination (collection_object_id)")
    op.execute("CREATE INDEX ix_td_taxon_id ON taxon_determination (taxon_id)")
    op.execute("CREATE INDEX ix_ba_relationship_id ON biological_association (biological_relationship_id)")
    op.execute("CREATE INDEX ix_ba_subject_co ON biological_association (subject_collection_object_id)")
    op.execute("CREATE INDEX ix_ba_subject_taxon ON biological_association (subject_taxon_id)")
    op.execute("CREATE INDEX ix_ba_object_co ON biological_association (object_collection_object_id)")
    op.execute("CREATE INDEX ix_ba_object_taxon ON biological_association (object_taxon_id)")

    # Seed biological relationship types
    now = "datetime('now')"
    op.execute(f"""
    INSERT INTO biological_relationship (name, inverted_name, definition, is_transitive, is_reflexive, created_at, updated_at)
    VALUES
        ('collected_on',    'host_of',         'Specimen collected on or from the host taxon',         0, 0, {now}, {now}),
        ('feeds_on',        'fed_on_by',        'Subject feeds on object',                              0, 0, {now}, {now}),
        ('parasitizes',     'parasitized_by',   'Subject parasitizes object',                           0, 0, {now}, {now}),
        ('reared_from',     'rearing_host_of',  'Specimen was reared from the host taxon',              0, 0, {now}, {now}),
        ('associated_with', 'associated_with',  'General co-occurrence or ecological association',      0, 0, {now}, {now})
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS biological_association")
    op.execute("DROP TABLE IF EXISTS biological_relationship")
    op.execute("DROP TABLE IF EXISTS taxon_determination")
    op.execute("DROP TABLE IF EXISTS identifier")
    op.execute("DROP TABLE IF EXISTS collection_object")
    op.execute("DROP TABLE IF EXISTS collecting_event")
    op.execute("DROP TABLE IF EXISTS taxon")
