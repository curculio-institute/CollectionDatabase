"""annotation_term + annotation; TaxonWorks link columns on biological_relationship

Tags and data attributes are a LOCAL controlled vocabulary (``annotation_term``). A term
may carry one optional link to a TaxonWorks keyword (tag) or predicate (data attribute):
``tw_id`` + ``tw_name`` (the TW name as last fetched — it becomes the export header) +
``tw_host`` (the host the link was confirmed on; NULL = not verified). ``css_color`` is the
chip colour — mirrored from TW while linked, the user's own otherwise.

``annotation`` attaches a term to exactly one specimen, collecting event or biological
association (exclusive arc, ON DELETE CASCADE). The term is referenced by the composite
FK ``(term_id, term_kind) -> annotation_term(id, kind)``, so a tag can never carry a
value, a data attribute must, and a used term cannot change kind (no ON UPDATE action).

``biological_relationship`` gains the same link columns (``taxonworksID`` is the link's
``tw_id``) by native ADD COLUMN. The ``tw_name`` <-> ``taxonworksID`` coupling is two
triggers, not a CHECK: SQLite tests an ADD COLUMN CHECK against every existing row, and a
linked row has no ``tw_name`` until the backfill below runs (measured, SQLite 3.53 — the
CHECK is refused). Existing links are backfilled with ``tw_name = name`` and stay
**unverified** (``tw_host`` NULL): the old launch sync recorded neither host nor time, and
both sfg and sandbox have been configured in the past.

Created fresh / added natively — no rebuild — so STRICT, every CHECK, UNIQUE and FK
action is declared here directly (DB-1 discipline). The upgrade REFUSES, naming the rows,
when two relationships share a taxonworksID or a linked name could not be a TW header.
The downgrade refuses while any annotation term exists.

Revision ID: 0073
Revises: 0072
"""
from typing import Union

from alembic import op

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels = None
depends_on = None


def _clean(col: str) -> str:
    """SQL: the column contains no tab, LF or CR (it becomes a TSV header / cell)."""
    return (f"instr({col}, char(9)) = 0 AND instr({col}, char(10)) = 0 "
            f"AND instr({col}, char(13)) = 0")


_HEX = "[0-9A-Fa-f]"
_COLOUR_CHECK = (f"css_color IS NULL OR css_color GLOB '#{_HEX * 3}' "
                 f"OR css_color GLOB '#{_HEX * 6}'")

_BR_TRIGGER_BODY = '''
WHEN (NEW."taxonworksID" IS NULL) <> (NEW.tw_name IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'biological_relationship: tw_name must be set exactly when taxonworksID is set');
END'''

_BR_TRIGGERS = (
    "CREATE TRIGGER trg_br_tw_link_ins BEFORE INSERT ON biological_relationship"
    + _BR_TRIGGER_BODY,
    'CREATE TRIGGER trg_br_tw_link_upd BEFORE UPDATE OF "taxonworksID", tw_name '
    "ON biological_relationship" + _BR_TRIGGER_BODY,
)


def _refuse_unrepresentable_relationships(bind) -> None:
    dupes = bind.exec_driver_sql(
        'SELECT "taxonworksID", group_concat(name, \', \') FROM biological_relationship '
        'WHERE "taxonworksID" IS NOT NULL GROUP BY "taxonworksID" HAVING COUNT(*) > 1'
    ).fetchall()
    if dupes:
        listed = "; ".join(f"id {tw_id}: {names}" for tw_id, names in dupes)
        raise RuntimeError(
            "0073: two biological relationships carry the same TaxonWorks id, so the new "
            f"unique link cannot be created ({listed}). Merge or unlink them first. "
            "Nothing is committed.")
    bad = bind.exec_driver_sql(
        'SELECT name FROM biological_relationship WHERE "taxonworksID" IS NOT NULL '
        f"AND (length(trim(name)) = 0 OR NOT ({_clean('name')}))"
    ).fetchall()
    if bad:
        raise RuntimeError(
            "0073: linked relationship name(s) contain a tab or line break and cannot be "
            f"copied into tw_name: {[r[0] for r in bad]!r}. Nothing is committed.")


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_unrepresentable_relationships(bind)

    op.execute(f"""
        CREATE TABLE annotation_term (
            id                INTEGER PRIMARY KEY,
            kind              TEXT    NOT NULL,
            name              TEXT    NOT NULL,
            allow_specimen    INTEGER NOT NULL DEFAULT 0,
            allow_event       INTEGER NOT NULL DEFAULT 0,
            allow_association INTEGER NOT NULL DEFAULT 0,
            tw_id             INTEGER,
            tw_name           TEXT,
            tw_host           TEXT,
            css_color         TEXT,
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL,
            CONSTRAINT uq_annotation_term_kind_name  UNIQUE (kind, name),
            CONSTRAINT uq_annotation_term_kind_tw_id UNIQUE (kind, tw_id),
            CONSTRAINT uq_annotation_term_id_kind    UNIQUE (id, kind),
            CONSTRAINT ck_annotation_term_kind CHECK (kind IN ('tag', 'data_attribute')),
            CONSTRAINT ck_annotation_term_name_not_blank CHECK (length(trim(name)) > 0),
            CONSTRAINT ck_annotation_term_allow_specimen CHECK (allow_specimen IN (0, 1)),
            CONSTRAINT ck_annotation_term_allow_event CHECK (allow_event IN (0, 1)),
            CONSTRAINT ck_annotation_term_allow_association CHECK (allow_association IN (0, 1)),
            CONSTRAINT ck_annotation_term_tw_name_iff_id CHECK ((tw_id IS NULL) = (tw_name IS NULL)),
            CONSTRAINT ck_annotation_term_tw_name_clean CHECK (
                tw_name IS NULL OR (length(trim(tw_name)) > 0 AND {_clean('tw_name')})),
            CONSTRAINT ck_annotation_term_tw_host_needs_id CHECK (tw_host IS NULL OR tw_id IS NOT NULL),
            CONSTRAINT ck_annotation_term_css_color CHECK ({_COLOUR_CHECK})
        ) STRICT
    """)
    op.execute(f"""
        CREATE TABLE annotation (
            id                        INTEGER PRIMARY KEY,
            collection_object_id      INTEGER REFERENCES collection_object(id) ON DELETE CASCADE,
            collecting_event_id       INTEGER REFERENCES collecting_event(id) ON DELETE CASCADE,
            biological_association_id INTEGER REFERENCES biological_association(id) ON DELETE CASCADE,
            term_id                   INTEGER NOT NULL,
            term_kind                 TEXT    NOT NULL,
            value                     TEXT,
            created_at                TEXT    NOT NULL,
            updated_at                TEXT    NOT NULL,
            CONSTRAINT ck_annotation_exclusive_arc CHECK (
                ((collection_object_id IS NOT NULL) + (collecting_event_id IS NOT NULL)
                 + (biological_association_id IS NOT NULL)) = 1),
            CONSTRAINT ck_annotation_kind CHECK (term_kind IN ('tag', 'data_attribute')),
            CONSTRAINT ck_annotation_value_by_kind CHECK (
                (term_kind = 'tag' AND value IS NULL)
                OR (term_kind = 'data_attribute' AND value IS NOT NULL
                    AND length(trim(value)) > 0 AND {_clean('value')})),
            CONSTRAINT fk_annotation_term FOREIGN KEY (term_id, term_kind)
                REFERENCES annotation_term (id, kind) ON DELETE RESTRICT
        ) STRICT
    """)
    for col, target in (("collection_object_id", "specimen"),
                        ("collecting_event_id", "event"),
                        ("biological_association_id", "association")):
        op.execute(f"CREATE UNIQUE INDEX uq_annotation_{target}_term "
                   f"ON annotation ({col}, term_id) WHERE {col} IS NOT NULL")
    op.execute("CREATE INDEX ix_annotation_term ON annotation (term_id)")

    op.execute(
        "ALTER TABLE biological_relationship ADD COLUMN tw_name TEXT "
        "CONSTRAINT ck_br_tw_name_clean CHECK ("
        f"tw_name IS NULL OR (length(trim(tw_name)) > 0 AND {_clean('tw_name')}))")
    op.execute(
        "ALTER TABLE biological_relationship ADD COLUMN tw_host TEXT "
        'CONSTRAINT ck_br_tw_host_needs_id CHECK (tw_host IS NULL OR "taxonworksID" IS NOT NULL)')
    op.execute('UPDATE biological_relationship SET tw_name = name '
               'WHERE "taxonworksID" IS NOT NULL')
    op.execute('CREATE UNIQUE INDEX uq_br_taxonworks_id ON biological_relationship '
               '("taxonworksID") WHERE "taxonworksID" IS NOT NULL')
    for ddl in _BR_TRIGGERS:
        op.execute(ddl)


def downgrade() -> None:
    bind = op.get_bind()
    n = bind.exec_driver_sql("SELECT COUNT(*) FROM annotation_term").scalar()
    if n:
        raise RuntimeError(
            f"0073 downgrade refused: {n} annotation term(s) exist and the old schema "
            "cannot hold them. Delete them in Controlled Vocabularies first.")
    for name in ("trg_br_tw_link_ins", "trg_br_tw_link_upd"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP INDEX IF EXISTS uq_br_taxonworks_id")
    op.execute("ALTER TABLE biological_relationship DROP COLUMN tw_host")
    op.execute("ALTER TABLE biological_relationship DROP COLUMN tw_name")
    op.execute("DROP TABLE annotation")
    op.execute("DROP TABLE annotation_term")
