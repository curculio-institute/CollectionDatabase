"""taxon_determination — dateIdentified may not be an interval

An identification happens on a date, not over a span, and TaxonWorks refuses the
range outright: its DwC importer calls ``parse_iso_date(:dateIdentified)`` and raises
``"Date range for taxon determination is not supported."`` when an end date comes back
(TW @ 897f385, app/models/dataset_record/darwin_core/occurrence.rb:1395-1397). This was
caught only at export time by ``dwc_export._validate_row``, i.e. after the record had
been sitting in the database — so the row could not be exported and the user found out
long after typing it. Blocking it here makes the bad state unrepresentable instead
(CLAUDE.md §2: prefer a DB-enforced constraint over application-level hope).

Note the asymmetry, which is deliberate and mirrors TaxonWorks: ``collecting_event
."dwc:eventDate"`` **does** allow an interval (a collecting trip spans days, and TW
accepts it there) — this constraint is on the determination only.

SQLite cannot ALTER … ADD CONSTRAINT, so taxon_determination is REBUILT. Per the DB-1
discipline (CLAUDE.md "Migration discipline — never lose constraints") the new DDL
re-declares STRICT, all three existing CHECKs (is_current, identificationQualifier, the
subject exclusive arc), the is_current server default, every FK ON DELETE action, and
both per-subject partial-unique indexes verbatim from 0060 — plus the new CHECK.
tests/test_schema_integrity.py guards the result.

The CHECK is a plain "no '/' in the value" test rather than a date-shape validation:
SQLite CHECKs cannot parse dates, and the shape is already enforced on the way in by
``dates.parse_dwc_date`` (UI) and ``specimens._reject_interval`` (service). What the DB
must guarantee is the one thing TaxonWorks cannot accept, and an interval is exactly a
value containing '/'.

**Upgrade fails loudly if any row still holds an interval** — the rebuild's INSERT would
violate the new CHECK. That is correct (a silent rewrite of a recorded date would be the
§2 failure), but note it aborts ``db_bootstrap.upgrade_to_head()`` and therefore the app
launch, so the offending determination must be corrected in Records *before* upgrading.
At the time of writing the live database had exactly one such row and it was cleared by
hand first.

Revision ID: 0069
Revises: 0068
"""
from typing import Union

from alembic import op

from app.vocab import IDENTIFICATION_QUALIFIERS

revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels = None
depends_on = None

_QUAL_LIST = ", ".join(f"'{q}'" for q in IDENTIFICATION_QUALIFIERS)
_QUAL_CHECK = (
    'CONSTRAINT ck_td_identification_qualifier CHECK ('
    '"dwc:identificationQualifier" IS NULL '
    f'OR "dwc:identificationQualifier" IN ({_QUAL_LIST}))'
)
_ARC_CHECK = (
    'CONSTRAINT ck_td_subject_exclusive_arc CHECK ('
    '(collection_object_id IS NOT NULL AND field_occurrence_id IS NULL) OR '
    '(collection_object_id IS NULL AND field_occurrence_id IS NOT NULL))'
)
_DATE_CHECK = (
    'CONSTRAINT ck_td_date_identified_no_interval CHECK ('
    '"dwc:dateIdentified" IS NULL '
    'OR "dwc:dateIdentified" NOT LIKE \'%/%\')'
)

_COLS = ('id, collection_object_id, field_occurrence_id, taxon_id, '
         '"dwc:verbatimIdentification", "dwc:sex", "dwc:typeStatus", identified_by_id, '
         '"dwc:dateIdentified", "dwc:identificationQualifier", '
         '"dwc:identificationRemarks", is_current, created_at, updated_at')


def _new_table(name: str, *, with_date_check: bool) -> str:
    date_check = f",\n    {_DATE_CHECK}" if with_date_check else ""
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    collection_object_id INTEGER,
    field_occurrence_id INTEGER,
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
    {_QUAL_CHECK},
    {_ARC_CHECK}{date_check},
    FOREIGN KEY(collection_object_id) REFERENCES collection_object (id) ON DELETE CASCADE,
    FOREIGN KEY(field_occurrence_id) REFERENCES field_occurrence (id) ON DELETE CASCADE,
    FOREIGN KEY(taxon_id) REFERENCES taxon (id) ON DELETE RESTRICT,
    FOREIGN KEY(identified_by_id) REFERENCES person (id) ON DELETE RESTRICT
) STRICT'''


_INDEXES = [
    "CREATE INDEX ix_td_co_id ON taxon_determination (collection_object_id)",
    "CREATE INDEX ix_td_taxon_id ON taxon_determination (taxon_id)",
    "CREATE UNIQUE INDEX uq_td_one_current_per_co ON taxon_determination "
    "(collection_object_id) WHERE is_current = 1",
    "CREATE INDEX ix_td_fo_id ON taxon_determination (field_occurrence_id)",
    "CREATE UNIQUE INDEX uq_td_one_current_per_fo ON taxon_determination "
    "(field_occurrence_id) WHERE is_current = 1",
]


def _rebuild(*, with_date_check: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_new_table("taxon_determination_new",
                              with_date_check=with_date_check))
        op.execute(f"INSERT INTO taxon_determination_new ({_COLS}) "
                   f"SELECT {_COLS} FROM taxon_determination")
        op.execute("DROP TABLE taxon_determination")
        op.execute("ALTER TABLE taxon_determination_new RENAME TO taxon_determination")
        for stmt in _INDEXES:
            op.execute(stmt)
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    _rebuild(with_date_check=True)


def downgrade() -> None:
    _rebuild(with_date_check=False)
