"""collection_object.catalogNumber + repository.collectionCode become optional

A specimen held in someone else's collection often has no catalog number, and a
private collection ("Frank Lange collection") often has neither a collection code nor
an institution code. Until now all three had to be invented to record the specimen.

``collection_object``
* ``dwc:catalogNumber`` is nullable. ``ck_co_catalog_number_not_blank`` keeps ``''``
  out, so "no number" has exactly one spelling (NULL) — and so two numberless
  specimens can never collide on the unique constraint as two equal ``''`` would.
* ``UNIQUE(repository_id, dwc:catalogNumber)`` is unchanged. SQLite treats NULLs as
  distinct, so any number of numberless specimens share a collection while a filled
  number stays unique in it. (Deliberately the opposite of the geography vocab's
  ``IFNULL`` index, 0056: there NULL meant "the one uncoded row", here it means "no
  identity claimed".)

``repository``
* ``dwc:collectionCode`` is nullable (+ not-blank CHECK); its UNIQUE becomes a partial
  unique index over the non-NULL codes.
* ``collection_full_name`` becomes the identity: UNIQUE + not-blank CHECK.
* ``ck_repository_default_has_code``: the default collection's code is the
  catalog-number prefix, so a codeless default is unusable.

The own-collection invariant — *every specimen in the default collection has a catalog
number* — needs a second table, which a CHECK cannot see, so it is three BEFORE
triggers that RAISE(ABORT), from raw SQL too:

* ``trg_co_default_requires_catalog_number_ins`` / ``_upd`` on ``collection_object``
  (creating a numberless specimen in the default collection, or re-homing one into it)
* ``trg_repository_default_requires_catalog_numbers`` on ``repository`` (flagging as
  default a collection that already holds numberless specimens)

SQLite cannot relax NOT NULL or alter a UNIQUE, so both tables are REBUILT. Per the
DB-1 discipline the new DDL re-declares STRICT, every CHECK, every UNIQUE, every FK
ON DELETE action, the server defaults and the indexes. tests/test_schema_integrity.py
guards the result.

The upgrade REFUSES (rather than renaming or merging) when two existing collections
share a name. The downgrade refuses while any numberless specimen or codeless
collection exists — the old schema cannot represent them.

Revision ID: 0072
Revises: 0071
"""
from typing import Union

from alembic import op

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels = None
depends_on = None

_CO_COLS = ('id, collecting_event_id, "dwc:catalogNumber", repository_id, '
            '"dwc:basisOfRecord", "dwc:individualCount", "dwc:lifeStage", '
            'disposition_id, "dwc:materialEntityRemarks", preparation_id, '
            'confidential, "dwc:otherCatalogNumbers", created_at, updated_at')

_REPO_COLS = ('id, "dwc:institutionCode", institution_full_name, "dwc:collectionCode", '
              'collection_full_name, taxonworks_institution_id, taxonworks_collection_id, '
              'is_default, person_id, created_at, updated_at')

# Tables whose rows hang off collection_object with ON DELETE CASCADE — counted before
# and after the rebuild, so a DROP that ran with foreign keys still ON (and so cascaded)
# aborts the migration instead of quietly emptying them.
_CASCADE_CHILDREN = ("taxon_determination", "life_stage_record", "external_identifier",
                     "media_attachment", "print_queue")

_TRIGGER_NAMES = (
    "trg_co_default_requires_catalog_number_ins",
    "trg_co_default_requires_catalog_number_upd",
    "trg_repository_default_requires_catalog_numbers",
)

_CO_TRIGGER_BODY = '''
WHEN NEW."dwc:catalogNumber" IS NULL
     AND (SELECT is_default FROM repository WHERE id = NEW.repository_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'a specimen in the default (own) collection must have a catalogNumber');
END'''

_TRIGGERS = (
    "CREATE TRIGGER trg_co_default_requires_catalog_number_ins "
    "BEFORE INSERT ON collection_object" + _CO_TRIGGER_BODY,
    "CREATE TRIGGER trg_co_default_requires_catalog_number_upd "
    'BEFORE UPDATE OF repository_id, "dwc:catalogNumber" ON collection_object'
    + _CO_TRIGGER_BODY,
    '''CREATE TRIGGER trg_repository_default_requires_catalog_numbers
BEFORE UPDATE OF is_default ON repository
WHEN NEW.is_default = 1
     AND EXISTS (SELECT 1 FROM collection_object
                 WHERE repository_id = NEW.id AND "dwc:catalogNumber" IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'a collection holding specimens without a catalogNumber cannot be the default collection');
END''',
)


def _collection_object(name: str, *, optional: bool) -> str:
    not_blank = '''
    CONSTRAINT ck_co_catalog_number_not_blank CHECK ("dwc:catalogNumber" IS NULL OR length(trim("dwc:catalogNumber")) > 0),'''
    return f'''CREATE TABLE {name} (
    id INTEGER NOT NULL,
    collecting_event_id INTEGER,
    "dwc:catalogNumber" TEXT{"" if optional else " NOT NULL"},
    repository_id INTEGER NOT NULL,
    "dwc:basisOfRecord" TEXT DEFAULT 'PreservedSpecimen' NOT NULL,
    "dwc:individualCount" INTEGER DEFAULT 1 NOT NULL,
    "dwc:lifeStage" TEXT,
    disposition_id INTEGER,
    "dwc:materialEntityRemarks" TEXT,
    preparation_id INTEGER,
    confidential INTEGER NOT NULL DEFAULT 0
        CONSTRAINT ck_co_confidential CHECK (confidential IN (0, 1)),
    "dwc:otherCatalogNumbers" TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_co_repository_catalog UNIQUE (repository_id, "dwc:catalogNumber"),{not_blank if optional else ""}
    CONSTRAINT ck_co_individual_count_non_negative CHECK ("dwc:individualCount" >= 0),
    CONSTRAINT ck_co_basis_of_record CHECK ("dwc:basisOfRecord" IN ('PreservedSpecimen', 'FossilSpecimen', 'HumanObservation')),
    FOREIGN KEY(collecting_event_id) REFERENCES collecting_event (id) ON DELETE RESTRICT,
    FOREIGN KEY(preparation_id) REFERENCES preparation (id) ON DELETE RESTRICT,
    FOREIGN KEY(repository_id) REFERENCES repository (id) ON DELETE RESTRICT,
    FOREIGN KEY(disposition_id) REFERENCES disposition (id) ON DELETE RESTRICT
) STRICT'''


def _repository(name: str, *, optional: bool) -> str:
    if optional:
        code_col = '"dwc:collectionCode" TEXT'
        constraints = ''',
    CONSTRAINT ck_repository_collection_code_not_blank CHECK ("dwc:collectionCode" IS NULL OR length(trim("dwc:collectionCode")) > 0),
    CONSTRAINT ck_repository_name_not_blank CHECK (length(trim(collection_full_name)) > 0),
    CONSTRAINT ck_repository_default_has_code CHECK (is_default = 0 OR "dwc:collectionCode" IS NOT NULL),
    CONSTRAINT uq_repository_collection_full_name UNIQUE (collection_full_name)'''
    else:
        code_col = '"dwc:collectionCode" TEXT NOT NULL'
        constraints = ''',
    CONSTRAINT uq_repository_collection_code UNIQUE ("dwc:collectionCode")'''
    return f'''CREATE TABLE {name} (
    id INTEGER PRIMARY KEY,
    "dwc:institutionCode" TEXT,
    institution_full_name TEXT,
    {code_col},
    collection_full_name TEXT NOT NULL,
    taxonworks_institution_id INTEGER,
    taxonworks_collection_id INTEGER,
    is_default INTEGER NOT NULL DEFAULT 0
        CONSTRAINT ck_repository_is_default CHECK (is_default IN (0, 1)),
    person_id INTEGER REFERENCES person(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL{constraints}
) STRICT'''


def _child_counts(bind) -> dict[str, int]:
    return {t: bind.exec_driver_sql(f"SELECT COUNT(*) FROM {t}").scalar()
            for t in _CASCADE_CHILDREN}


def _rebuild(*, optional: bool) -> None:
    bind = op.get_bind()
    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    before = _child_counts(bind)

    bind.exec_driver_sql("PRAGMA foreign_keys = OFF")
    try:
        op.execute(_repository("repository_new", optional=optional))
        op.execute(f"INSERT INTO repository_new ({_REPO_COLS}) "
                   f"SELECT {_REPO_COLS} FROM repository")
        op.execute("DROP TABLE repository")
        op.execute("ALTER TABLE repository_new RENAME TO repository")
        op.execute("CREATE UNIQUE INDEX uq_repository_one_default "
                   "ON repository (is_default) WHERE is_default = 1")
        if optional:
            op.execute('CREATE UNIQUE INDEX uq_repository_collection_code '
                       'ON repository ("dwc:collectionCode") '
                       'WHERE "dwc:collectionCode" IS NOT NULL')

        op.execute(_collection_object("collection_object_new", optional=optional))
        op.execute(f"INSERT INTO collection_object_new ({_CO_COLS}) "
                   f"SELECT {_CO_COLS} FROM collection_object")
        op.execute("DROP TABLE collection_object")
        op.execute("ALTER TABLE collection_object_new RENAME TO collection_object")
        op.execute("CREATE INDEX ix_co_collecting_event_id "
                   "ON collection_object (collecting_event_id)")
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys = ON")

    # `PRAGMA foreign_keys = OFF` is a silent no-op inside an open transaction, and a
    # DROP TABLE with enforcement ON cascades into every child table. Prove it did not.
    after = _child_counts(bind)
    if after != before:
        raise RuntimeError(
            "0072: rows were lost from tables referencing collection_object during the "
            f"rebuild (before {before}, after {after}) — foreign keys were still "
            "enforced when the old table was dropped. Nothing is committed.")
    broken = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if broken:
        raise RuntimeError(f"0072: the rebuild left dangling foreign keys: {broken[:10]}")

    if optional:
        for ddl in _TRIGGERS:
            op.execute(ddl)


def upgrade() -> None:
    bind = op.get_bind()
    dupes = bind.exec_driver_sql(
        "SELECT collection_full_name, COUNT(*) FROM repository "
        "GROUP BY collection_full_name HAVING COUNT(*) > 1").fetchall()
    if dupes:
        names = ", ".join(f"{n!r} ({c}x)" for n, c in dupes)
        raise RuntimeError(
            "0072: the collection name becomes the collection's identity, but these "
            f"names are used by more than one collection: {names}. Give each a distinct "
            "name in Controlled Vocabularies -> Collections, then start again.")
    blank = bind.exec_driver_sql(
        "SELECT id FROM repository WHERE length(trim(collection_full_name)) = 0 "
        "OR length(trim(\"dwc:collectionCode\")) = 0").fetchall()
    if blank:
        raise RuntimeError(
            f"0072: collection row(s) {[r[0] for r in blank]} have a blank name or "
            "code. Fill them in Controlled Vocabularies -> Collections, then start again.")
    blank_cat = bind.exec_driver_sql(
        'SELECT COUNT(*) FROM collection_object '
        'WHERE length(trim("dwc:catalogNumber")) = 0').scalar()
    if blank_cat:
        raise RuntimeError(
            f"0072: {blank_cat} specimen(s) have a blank (empty-string) catalogNumber, "
            "which the new not-blank CHECK refuses. Resolve them first.")
    _rebuild(optional=True)


def downgrade() -> None:
    bind = op.get_bind()
    no_cat = bind.exec_driver_sql(
        'SELECT COUNT(*) FROM collection_object WHERE "dwc:catalogNumber" IS NULL').scalar()
    no_code = bind.exec_driver_sql(
        'SELECT COUNT(*) FROM repository WHERE "dwc:collectionCode" IS NULL').scalar()
    if no_cat or no_code:
        raise RuntimeError(
            f"0072 downgrade refused: {no_cat} specimen(s) without a catalogNumber and "
            f"{no_code} collection(s) without a collectionCode exist; the previous "
            "schema requires both and cannot represent them.")
    _rebuild(optional=False)
