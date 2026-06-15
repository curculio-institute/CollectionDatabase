"""restore STRICT, CHECK, UNIQUE, and FK ON DELETE constraints lost by recreate= migrations (DB-1, #1)

Migrations 0020/0021/0024-0027 rebuilt several tables with
``batch_alter_table(recreate=...)``, which reflects columns but silently drops
STRICT typing, CHECK/UNIQUE constraints, and FK ON DELETE actions. This migration
rebuilds the five affected tables with the full intended schema restored:

  * STRICT typing            (all five tables)
  * CHECK constraints        (ck_ce_* x5, ck_co_individual_count_non_negative,
                              ck_td_is_current_bool)
  * UNIQUE (collectionCode, catalogNumber) on collection_object
  * FK ON DELETE actions     (RESTRICT / CASCADE / SET NULL per the models)
  * label_code.batch_id -> label_batch FK (was missing entirely)

Column sets and server DEFAULTs are preserved verbatim. CHECK/UNIQUE constraints
are enforced on the INSERT...SELECT copy, so any pre-existing violating row aborts
the migration loudly (verified zero violations at authoring time).

A permanent guard against re-loss lives in tests/test_schema_integrity.py.

Revision ID: 0029
Revises: 0028
"""
from typing import Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels = None
depends_on = None

_TABLES = ["collecting_event", "collection_object", "taxon_determination", "label_code", "print_queue"]


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        # ---- collecting_event ----
        op.execute("""CREATE TABLE collecting_event_new (
        	id INTEGER NOT NULL, 
        	"dwc:verbatimLabel" TEXT, 
        	"dwc:continent" TEXT, 
        	"dwc:country" TEXT, 
        	"dwc:countryCode" TEXT, 
        	"dwc:stateProvince" TEXT, 
        	"dwc:county" TEXT, 
        	"dwc:municipality" TEXT, 
        	"dwc:island" TEXT, 
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
        	"dwc:habitat" TEXT, 
        	"dwc:samplingProtocol" TEXT, 
        	recorded_by_id INTEGER, 
        	"dwc:eventRemarks" TEXT, 
        	habitat_enriched TEXT, 
        	habitat_ambiguous INTEGER, 
        	created_at TEXT NOT NULL, 
        	updated_at TEXT NOT NULL, 
        	PRIMARY KEY (id), 
        	CONSTRAINT ck_ce_lat_range CHECK ("dwc:decimalLatitude" IS NULL OR ("dwc:decimalLatitude" >= -90.0 AND "dwc:decimalLatitude" <= 90.0)), 
        	CONSTRAINT ck_ce_lon_range CHECK ("dwc:decimalLongitude" IS NULL OR ("dwc:decimalLongitude" >= -180.0 AND "dwc:decimalLongitude" <= 180.0)), 
        	CONSTRAINT ck_ce_uncertainty_positive CHECK ("dwc:coordinateUncertaintyInMeters" IS NULL OR "dwc:coordinateUncertaintyInMeters" >= 0.0), 
        	CONSTRAINT ck_ce_country_code_len CHECK ("dwc:countryCode" IS NULL OR length("dwc:countryCode") = 2), 
        	CONSTRAINT ck_ce_habitat_ambiguous_bool CHECK (habitat_ambiguous IS NULL OR habitat_ambiguous IN (0, 1)), 
        	FOREIGN KEY(recorded_by_id) REFERENCES person (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute('INSERT INTO collecting_event_new (id, "dwc:verbatimLabel", "dwc:continent", "dwc:country", "dwc:countryCode", "dwc:stateProvince", "dwc:county", "dwc:municipality", "dwc:island", "dwc:locality", "dwc:verbatimLocality", "dwc:locationRemarks", "dwc:decimalLatitude", "dwc:decimalLongitude", "dwc:geodeticDatum", "dwc:coordinateUncertaintyInMeters", "dwc:coordinatePrecision", "dwc:verbatimCoordinates", "dwc:verbatimCoordinateSystem", "dwc:minimumElevationInMeters", "dwc:maximumElevationInMeters", "dwc:verbatimElevation", "dwc:georeferencedBy", "dwc:georeferencedDate", "dwc:georeferenceProtocol", "dwc:georeferenceSources", "dwc:georeferenceRemarks", "dwc:georeferenceVerificationStatus", "dwc:eventDate", "dwc:verbatimEventDate", "dwc:fieldNumber", "dwc:habitat", "dwc:samplingProtocol", recorded_by_id, "dwc:eventRemarks", habitat_enriched, habitat_ambiguous, created_at, updated_at) SELECT id, "dwc:verbatimLabel", "dwc:continent", "dwc:country", "dwc:countryCode", "dwc:stateProvince", "dwc:county", "dwc:municipality", "dwc:island", "dwc:locality", "dwc:verbatimLocality", "dwc:locationRemarks", "dwc:decimalLatitude", "dwc:decimalLongitude", "dwc:geodeticDatum", "dwc:coordinateUncertaintyInMeters", "dwc:coordinatePrecision", "dwc:verbatimCoordinates", "dwc:verbatimCoordinateSystem", "dwc:minimumElevationInMeters", "dwc:maximumElevationInMeters", "dwc:verbatimElevation", "dwc:georeferencedBy", "dwc:georeferencedDate", "dwc:georeferenceProtocol", "dwc:georeferenceSources", "dwc:georeferenceRemarks", "dwc:georeferenceVerificationStatus", "dwc:eventDate", "dwc:verbatimEventDate", "dwc:fieldNumber", "dwc:habitat", "dwc:samplingProtocol", recorded_by_id, "dwc:eventRemarks", habitat_enriched, habitat_ambiguous, created_at, updated_at FROM collecting_event')
        op.execute("DROP TABLE collecting_event")
        op.execute("ALTER TABLE collecting_event_new RENAME TO collecting_event")

        # ---- collection_object ----
        op.execute("""CREATE TABLE collection_object_new (
        	id INTEGER NOT NULL, 
        	collecting_event_id INTEGER, 
        	"dwc:catalogNumber" TEXT NOT NULL, 
        	"dwc:collectionCode" TEXT NOT NULL, 
        	"dwc:institutionCode" TEXT DEFAULT '' NOT NULL, 
        	"dwc:basisOfRecord" TEXT DEFAULT 'PreservedSpecimen' NOT NULL, 
        	"dwc:individualCount" INTEGER DEFAULT 1 NOT NULL, 
        	"dwc:lifeStage" TEXT, 
        	"dwc:disposition" TEXT, 
        	"dwc:preparations" TEXT, 
        	"dwc:materialEntityRemarks" TEXT, 
        	created_at TEXT NOT NULL, 
        	updated_at TEXT NOT NULL, 
        	PRIMARY KEY (id), 
        	CONSTRAINT uq_co_collection_catalog UNIQUE ("dwc:collectionCode", "dwc:catalogNumber"), 
        	CONSTRAINT ck_co_individual_count_non_negative CHECK ("dwc:individualCount" >= 0), 
        	CONSTRAINT ck_co_basis_of_record CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')), 
        	CONSTRAINT ck_co_disposition CHECK ("dwc:disposition" IS NULL OR "dwc:disposition" IN ('in collection', 'on loan', 'donated', 'exchanged', 'missing', 'destroyed')), 
        	FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute('INSERT INTO collection_object_new (id, collecting_event_id, "dwc:catalogNumber", "dwc:collectionCode", "dwc:institutionCode", "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", "dwc:disposition", "dwc:preparations", "dwc:materialEntityRemarks", created_at, updated_at) SELECT id, collecting_event_id, "dwc:catalogNumber", "dwc:collectionCode", "dwc:institutionCode", "dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", "dwc:disposition", "dwc:preparations", "dwc:materialEntityRemarks", created_at, updated_at FROM collection_object')
        op.execute("DROP TABLE collection_object")
        op.execute("ALTER TABLE collection_object_new RENAME TO collection_object")
        op.execute("""CREATE INDEX ix_co_collecting_event_id ON collection_object (collecting_event_id)""")

        # ---- taxon_determination ----
        op.execute("""CREATE TABLE taxon_determination_new (
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
        	CONSTRAINT ck_td_is_current_bool CHECK (is_current IN (0, 1)), 
        	FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE, 
        	FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT, 
        	FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT
        ) STRICT""")
        op.execute('INSERT INTO taxon_determination_new (id, collection_object_id, taxon_id, "dwc:verbatimIdentification", "dwc:sex", "dwc:typeStatus", identified_by_id, "dwc:dateIdentified", "dwc:identificationQualifier", "dwc:identificationRemarks", is_current, created_at, updated_at) SELECT id, collection_object_id, taxon_id, "dwc:verbatimIdentification", "dwc:sex", "dwc:typeStatus", identified_by_id, "dwc:dateIdentified", "dwc:identificationQualifier", "dwc:identificationRemarks", is_current, created_at, updated_at FROM taxon_determination')
        op.execute("DROP TABLE taxon_determination")
        op.execute("ALTER TABLE taxon_determination_new RENAME TO taxon_determination")
        op.execute("""CREATE INDEX ix_td_co_id ON taxon_determination (collection_object_id)""")
        op.execute("""CREATE INDEX ix_td_taxon_id ON taxon_determination (taxon_id)""")

        # ---- label_code ----
        op.execute("""CREATE TABLE label_code_new (
        	id INTEGER NOT NULL, 
        	code TEXT NOT NULL, 
        	status TEXT DEFAULT 'reserved' NOT NULL, 
        	collection_object_id INTEGER, 
        	batch_id INTEGER, 
        	created_at TEXT NOT NULL, 
        	updated_at TEXT NOT NULL, 
        	PRIMARY KEY (id), 
        	CONSTRAINT ck_label_code_status CHECK (status IN ('reserved', 'assigned')), 
        	UNIQUE (code), 
        	FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE SET NULL, 
        	FOREIGN KEY(batch_id) REFERENCES label_batch (id)
        ) STRICT""")
        op.execute('INSERT INTO label_code_new (id, code, status, collection_object_id, batch_id, created_at, updated_at) SELECT id, code, status, collection_object_id, batch_id, created_at, updated_at FROM label_code')
        op.execute("DROP TABLE label_code")
        op.execute("ALTER TABLE label_code_new RENAME TO label_code")

        # ---- print_queue ----
        op.execute("""CREATE TABLE print_queue_new (
        	id INTEGER NOT NULL, 
        	label_type TEXT NOT NULL, 
        	print_group_id INTEGER, 
        	source TEXT, 
        	collection_object_id INTEGER, 
        	label_code_id INTEGER, 
        	created_at TEXT NOT NULL, 
        	updated_at TEXT NOT NULL, 
        	PRIMARY KEY (id), 
        	CONSTRAINT ck_print_queue_label_type CHECK (label_type IN ('data', 'determination', 'identifier')), 
        	CONSTRAINT ck_print_queue_exclusive_arc CHECK ((label_type IN ('data', 'determination')  AND collection_object_id IS NOT NULL  AND label_code_id IS NULL) OR (label_type = 'identifier'  AND label_code_id IS NOT NULL  AND collection_object_id IS NULL)), 
        	FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE, 
        	FOREIGN KEY(label_code_id) REFERENCES label_code (id) ON DELETE CASCADE
        ) STRICT""")
        op.execute('INSERT INTO print_queue_new (id, label_type, print_group_id, source, collection_object_id, label_code_id, created_at, updated_at) SELECT id, label_type, print_group_id, source, collection_object_id, label_code_id, created_at, updated_at FROM print_queue')
        op.execute("DROP TABLE print_queue")
        op.execute("ALTER TABLE print_queue_new RENAME TO print_queue")

        rows = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
        if rows:
            raise RuntimeError(f"FK check failed after constraint restore: {rows}")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def downgrade() -> None:
    # Intentional no-op: restores the schema's *intended* constraints; reversing
    # would re-introduce the DB-1 regression.
    pass
