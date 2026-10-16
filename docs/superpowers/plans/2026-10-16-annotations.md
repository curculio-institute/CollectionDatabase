# Annotations (tags, data attributes, TaxonWorks-linked vocabularies) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let specimens, collecting events and biological associations carry tags and data
attributes from a local controlled vocabulary whose terms may optionally link to one
TaxonWorks keyword / predicate; show them on the record sheets, export linked ones in the
occurrence TSV behind a term pre-flight, and report divergence in Compare. Biological
relationships get the same optional-link mechanism, and the silent launch sync is removed.

**Architecture:** One migration (0073) adds `annotation_term` + `annotation` and two link
columns on `biological_relationship`. Services are split by responsibility:
`annotations.py` (terms + annotations, local only), `tw_links.py` (fetch / link / verify for
all three vocabularies), `annotation_export.py` (export columns + term pre-flight),
`tw_annotation_compare.py` (Compare, built like `tw_media_compare.py`). The UI gets one
annotation button component used in Digitize / Mounting / Records, chips on the record
sheets, and two new Controlled Vocabularies sub-tabs.

**Tech Stack:** Python 3.12 · SQLite (STRICT) via SQLAlchemy 2.0 · Alembic · NiceGUI 2 ·
httpx (async, `MockTransport` in tests) · pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-annotations-design.md` (approved 2026-10-16).
Read it before starting any task; this plan argues from it.

## Global Constraints

- Conda env `collection`; run tests with `conda run -n collection python -m pytest -q`.
  Never touch the `phylogeny` or `catalogue` envs.
- Work on **one** branch, `feature/annotations`, created from `main` before Task 1
  (`git checkout -b feature/annotations`). Commit after every task.
- **Migration 0073 applies itself to the live DB the next time the user launches the app
  from this checkout** (`db_bootstrap.upgrade_to_head` in `run.py`). Tell the user when
  Task 1 lands. Never run the app or any script against `data/collection.db`; never seed
  data into it; **ask before any DB-related action**, even on a copy.
- Every new table is `STRICT`; every constraint is declared in migration DDL (models cannot
  express STRICT). `PRAGMA foreign_keys = ON` is already set on every connection.
- **Refuse loudly, never skip / null / guess** (CLAUDE.md §2). Errors name the cause.
- **TaxonWorks facts are cited** in a code comment as `file:line` at commit `897f385` or as
  an API route. Re-grep line numbers on GitHub before citing; do not use a local clone.
- **Unknown TW query params are silently ignored** — every TW response used for a decision
  is re-verified (requested type / ids only); an oversized or short read is a lookup
  failure (`TaxonWorksUnreachable`), never a result.
- Export header uses **`tw_name`**, never the local name. Association annotations are
  never exported. Local-only terms are never exported, never compared.
- UI: empty option last in every `ui.select`; dialogs created per action get
  `dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)`; DB-backed selects
  refresh via `ui.timer` + `set_options`; "label", never "sticker".
- Post-commit cleanup (notify, refresh, `on_saved`) goes **outside** the try/except, with an
  explicit `return` in the except.
- Never leave a server running on port 8080. UI verification uses Playwright on a spare
  port against a **copy** of the DB, only after the user says yes.
- `main.py` is the page shell: land a module before wiring its import into `main.py` (the
  user launches the app unannounced).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **TW pages without `ORDER BY`** (`/controlled_vocabulary_terms`, `/data_attributes`):
   a row repeated across pages must not hide a skipped one — rows are de-duplicated by id
   and a pull with fewer distinct ids than `pagination-total` raises. Pinned in Task 3
   (`test_fetch_all_pages_repeated_rows_are_a_lookup_failure`).
2. **Merging two terms that sit on the same record with different data-attribute values**
   must refuse and name the record, not keep one value at random. Pinned in Task 2
   (`test_merge_refuses_conflicting_values`).
3. **A staged Digitize data attribute on a reused event that already has another value**
   must refuse the Save with both values named, writing nothing. Pinned in Task 2
   (`test_add_annotations_conflicting_value_refuses`) and Task 10 (the call sits inside the
   Save transaction).
4. **A term verified on sfg after the user switches Settings to the sandbox** must block
   the export and be listed as "not compared" in Compare — never joined on its stale id.
   Pinned in Task 5 (`test_term_verified_on_another_host_blocks`) and Task 7
   (`test_unverified_term_is_listed_not_joined`).
5. **A TaxonWorks `css_color` that is not a hex colour** (`red`, `#12345`, blank) must
   leave the colour NULL with a visible warning and the link intact. Pinned in Task 3
   (`test_link_with_unusable_colour_keeps_link_and_warns`).

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `alembic/versions/0073_annotations.py` | create | schema: two tables, link columns, triggers, partial unique index |
| `app/models/annotation.py` | create | `AnnotationTerm`, `Annotation` |
| `app/models/biological.py` | modify | `tw_name`, `tw_host`, `tw_id` synonym on `BiologicalRelationship` |
| `app/services/annotations.py` | create | term CRUD / merge, colour + value validation, set/add/list annotations |
| `app/services/taxonworks.py` | modify | `configured_host`, `get_with_retry`, `fetch_all_pages`; drop `fetch_biological_relationships` |
| `app/services/tw_links.py` | create | fetch candidates, link / unlink / verify for keyword, predicate, relationship |
| `app/services/biological.py` | modify | remove launch sync; `relationship_vocab`, `create_relationship`, `merge_relationships` |
| `app/services/annotation_export.py` | create | export columns + cells, term pre-flight |
| `app/services/dwc_export.py` | modify | append annotation columns; `ExportResult` fields |
| `app/services/tw_annotation_compare.py` | create | Compare: fetch by object, pure compare, entry point |
| `app/ui/merge_target_list.py` | create | the shared "entry to keep" picker, moved out of `controlled_vocab_tab.py` |
| `app/ui/annotation_chips.py` | create | chip / text colour rendering (pure HTML helpers) |
| `app/ui/tw_vocab_section.py` | create | "Tags & data attributes" + "Biological relationships" sub-tabs |
| `app/ui/annotation_panel.py` | create | footer button + popup (staged / deferred / bound) |
| `app/ui/controlled_vocab_tab.py`, `record_sheet.py`, `digitize_tab.py`, `mounting_session.py`, `records_tab.py`, `tw_sync_tab.py`, `main.py` | modify | wiring |
| `docs/schema.html`, `docs/data_model.md`, `docs/taxonworks.md`, `docs/app_structure.md`, `docs/design.md`, `CLAUDE.md` | modify | docs |

---

### Task 1: Schema — migration 0073 and models

**Files:**
- Create: `alembic/versions/0073_annotations.py`
- Create: `app/models/annotation.py`
- Modify: `app/models/biological.py` (class `BiologicalRelationship`)
- Modify: `app/models/__init__.py`
- Modify: `tests/test_schema_integrity.py` (`STRICT_TABLES`, new trigger test)
- Create: `tests/test_annotation_schema.py`
- Modify: `docs/schema.html`, `docs/data_model.md`

**Interfaces:**
- Produces: tables `annotation_term`, `annotation`; columns `biological_relationship.tw_name`,
  `.tw_host`; models `AnnotationTerm(id, kind, name, allow_specimen, allow_event,
  allow_association, tw_id, tw_name, tw_host, css_color)`, `Annotation(id,
  collection_object_id, collecting_event_id, biological_association_id, term_id, term_kind,
  value, term)`; `BiologicalRelationship.tw_id` (synonym of `taxonworks_id`), `.tw_name`,
  `.tw_host`.

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_annotation_schema.py`:

```python
"""Migration 0073: annotation_term + annotation, and the TaxonWorks link columns on
biological_relationship. Every rule here is DB-enforced — the service layer adds clearer
messages, but a raw INSERT must fail just the same (CLAUDE.md §2)."""
import sqlite3

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import CollectionObject
from tests.helpers import ensure_repo

NOW = "2026-10-16T00:00:00+00:00"


def _insert(s, table: str, **cols) -> int:
    cols = {"created_at": NOW, "updated_at": NOW, **cols}
    keys = ", ".join(f'"{k}"' for k in cols)
    params = ", ".join(f":{k}" for k in cols)
    return s.execute(
        text(f"INSERT INTO {table} ({keys}) VALUES ({params}) RETURNING id"), cols
    ).scalar_one()


def _fails(s, table: str, **cols) -> None:
    with pytest.raises(IntegrityError):
        with s.begin_nested():
            _insert(s, table, **cols)


def _specimen(s, cat: str) -> int:
    co = CollectionObject(catalog_number=cat, repository_id=ensure_repo(s, "ANN"))
    s.add(co)
    s.flush()
    return co.id


# ── annotation_term ────────────────────────────────────────────────────────────

def test_term_kind_is_closed(session):
    _fails(session, "annotation_term", kind="note", name="x")


def test_term_name_not_blank(session):
    _fails(session, "annotation_term", kind="tag", name="   ")


def test_term_name_unique_per_kind(session):
    _insert(session, "annotation_term", kind="tag", name="Adventive")
    _insert(session, "annotation_term", kind="data_attribute", name="Adventive")
    _fails(session, "annotation_term", kind="tag", name="Adventive")


def test_tw_name_iff_tw_id(session):
    _fails(session, "annotation_term", kind="tag", name="a", tw_id=1)
    _fails(session, "annotation_term", kind="tag", name="b", tw_name="Adventive")


def test_tw_host_needs_tw_id(session):
    _fails(session, "annotation_term", kind="tag", name="a", tw_host="sfg.taxonworks.org")


@pytest.mark.parametrize("bad", ["Adv\tentive", "Adv\nentive", "Adv\rentive", "  "])
def test_tw_name_clean(session, bad):
    _fails(session, "annotation_term", kind="tag", name="a", tw_id=1, tw_name=bad)


def test_tw_id_unique_per_kind(session):
    _insert(session, "annotation_term", kind="tag", name="a", tw_id=7, tw_name="A")
    _insert(session, "annotation_term", kind="data_attribute", name="b", tw_id=7,
            tw_name="B")
    _fails(session, "annotation_term", kind="tag", name="c", tw_id=7, tw_name="C")


@pytest.mark.parametrize("good", ["#abc", "#AABBCC", "#254d0d"])
def test_css_color_accepts_hex(session, good):
    _insert(session, "annotation_term", kind="tag", name=f"t{good}", css_color=good)


@pytest.mark.parametrize("bad", ["red", "#abcd", "abc", "#12345", "#ggg", ""])
def test_css_color_rejects_non_hex(session, bad):
    _fails(session, "annotation_term", kind="tag", name="t", css_color=bad)


# ── annotation ─────────────────────────────────────────────────────────────────

def test_annotation_exclusive_arc(session):
    t = _insert(session, "annotation_term", kind="tag", name="t")
    _fails(session, "annotation", term_id=t, term_kind="tag")             # no target
    co = _specimen(session, "ANN-00001")
    ev = _insert(session, "collecting_event")
    _fails(session, "annotation", term_id=t, term_kind="tag",
           collection_object_id=co, collecting_event_id=ev)              # two targets


def test_value_by_kind(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    da = _insert(session, "annotation_term", kind="data_attribute", name="d")
    co = _specimen(session, "ANN-00002")
    _fails(session, "annotation", term_id=tag, term_kind="tag",
           collection_object_id=co, value="x")                           # tag with value
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co)                                      # DA without value
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co, value="  ")
    _fails(session, "annotation", term_id=da, term_kind="data_attribute",
           collection_object_id=co, value="a\tb")


def test_composite_fk_rejects_kind_mismatch(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00003")
    _fails(session, "annotation", term_id=tag, term_kind="data_attribute",
           collection_object_id=co, value="x")


def test_used_term_cannot_be_deleted_or_change_kind(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00004")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text("DELETE FROM annotation_term WHERE id = :i"), {"i": tag})
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text("UPDATE annotation_term SET kind = 'data_attribute' "
                                 "WHERE id = :i"), {"i": tag})


def test_one_annotation_per_record_and_term(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00005")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    _fails(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)


def test_deleting_the_specimen_cascades(session):
    tag = _insert(session, "annotation_term", kind="tag", name="t")
    co = _specimen(session, "ANN-00006")
    _insert(session, "annotation", term_id=tag, term_kind="tag", collection_object_id=co)
    session.execute(text("DELETE FROM collection_object WHERE id = :i"), {"i": co})
    assert session.execute(text("SELECT COUNT(*) FROM annotation WHERE term_id = :t"),
                           {"t": tag}).scalar() == 0


# ── biological_relationship link columns ───────────────────────────────────────

def test_relationship_tw_name_iff_id_is_a_trigger(session):
    _fails(session, "biological_relationship", name="feeds on", taxonworksID=11)
    rid = _insert(session, "biological_relationship", name="feeds on")
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text('UPDATE biological_relationship SET "taxonworksID" = 11 '
                                 "WHERE id = :i"), {"i": rid})


def test_relationship_tw_host_needs_id(session):
    _fails(session, "biological_relationship", name="x", tw_host="sfg.taxonworks.org")


def test_relationship_taxonworks_id_unique_among_set(session):
    _insert(session, "biological_relationship", name="a", taxonworksID=5, tw_name="A")
    _insert(session, "biological_relationship", name="b")
    _insert(session, "biological_relationship", name="c")       # NULLs never collide
    _fails(session, "biological_relationship", name="d", taxonworksID=5, tw_name="D")


# ── migration behaviour on existing data ───────────────────────────────────────

def _cfg(tmp_path) -> tuple[Config, str]:
    db = tmp_path / "m.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg, str(db)


def test_existing_links_are_backfilled_unverified(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0072")
    con = sqlite3.connect(db)
    con.execute('INSERT INTO biological_relationship (name, "taxonworksID", created_at, '
                "updated_at) VALUES ('feeds on', 11, ?, ?)", (NOW, NOW))
    con.execute("INSERT INTO biological_relationship (name, created_at, updated_at) "
                "VALUES ('local only', ?, ?)", (NOW, NOW))
    con.commit()
    con.close()
    upgrade(cfg, "0073")
    con = sqlite3.connect(db)
    rows = dict((n, (tn, th)) for n, tn, th in con.execute(
        "SELECT name, tw_name, tw_host FROM biological_relationship"))
    con.close()
    assert rows["feeds on"] == ("feeds on", None)       # name copied, NOT verified
    assert rows["local only"] == (None, None)


def test_upgrade_refuses_duplicate_relationship_ids(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0072")
    con = sqlite3.connect(db)
    for name in ("feeds on", "eats"):
        con.execute('INSERT INTO biological_relationship (name, "taxonworksID", '
                    "created_at, updated_at) VALUES (?, 11, ?, ?)", (name, NOW, NOW))
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="id 11"):
        upgrade(cfg, "0073")


def test_downgrade_round_trips_an_empty_vocabulary(tmp_path):
    cfg, db = _cfg(tmp_path)
    upgrade(cfg, "0073")
    downgrade(cfg, "0072")
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(biological_relationship)")}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "tw_name" not in cols and "tw_host" not in cols
    assert "annotation_term" not in tables and "annotation" not in tables
```

Add to `tests/test_schema_integrity.py` — in `STRICT_TABLES` add a line
`"annotation_term", "annotation",  # 0073` after `"disposition",  # 0048`, and append:

```python
def test_relationship_link_triggers_present(engine):
    # Migration 0073: SQLite tests an ADD COLUMN CHECK against existing rows, and a linked
    # relationship has no tw_name until the backfill runs — so the tw_name <-> taxonworksID
    # coupling is two triggers. A rebuild of biological_relationship would drop them.
    with engine.connect() as conn:
        live = dict(conn.exec_driver_sql(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='trigger'").fetchall())
    for name in ("trg_br_tw_link_ins", "trg_br_tw_link_upd"):
        assert live.get(name) == "biological_relationship", f"lost trigger {name}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_schema.py tests/test_schema_integrity.py`
Expected: FAIL — `no such table: annotation_term` / `Can't locate revision 0073`.

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0073_annotations.py`:

```python
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
```

- [ ] **Step 4: Write the models**

Create `app/models/annotation.py`:

```python
from __future__ import annotations

from typing import Optional

from sqlalchemy import (CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

_CLEAN = ("instr({c}, char(9)) = 0 AND instr({c}, char(10)) = 0 "
          "AND instr({c}, char(13)) = 0")
_HEX = "[0-9A-Fa-f]"


class AnnotationTerm(Base, TimestampMixin):
    """A tag or data-attribute term of the LOCAL controlled vocabulary (migration 0073).

    Optional link to ONE TaxonWorks keyword (tag) / predicate (data attribute):
    ``tw_id`` + ``tw_name`` (TW's name as last fetched; the export header) + ``tw_host``
    (confirmed on that host; NULL = not verified). ``tw_id`` NULL = local only, never
    exported. ``css_color`` is mirrored from TW while linked (``tw_links``)."""

    __tablename__ = "annotation_term"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    allow_specimen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_event: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_association: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tw_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tw_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tw_host: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    css_color: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_annotation_term_kind_name"),
        UniqueConstraint("kind", "tw_id", name="uq_annotation_term_kind_tw_id"),
        UniqueConstraint("id", "kind", name="uq_annotation_term_id_kind"),
        CheckConstraint("kind IN ('tag', 'data_attribute')", name="ck_annotation_term_kind"),
        CheckConstraint("length(trim(name)) > 0", name="ck_annotation_term_name_not_blank"),
        CheckConstraint("allow_specimen IN (0, 1)", name="ck_annotation_term_allow_specimen"),
        CheckConstraint("allow_event IN (0, 1)", name="ck_annotation_term_allow_event"),
        CheckConstraint("allow_association IN (0, 1)",
                        name="ck_annotation_term_allow_association"),
        CheckConstraint("(tw_id IS NULL) = (tw_name IS NULL)",
                        name="ck_annotation_term_tw_name_iff_id"),
        CheckConstraint("tw_name IS NULL OR (length(trim(tw_name)) > 0 AND "
                        + _CLEAN.format(c="tw_name") + ")",
                        name="ck_annotation_term_tw_name_clean"),
        CheckConstraint("tw_host IS NULL OR tw_id IS NOT NULL",
                        name="ck_annotation_term_tw_host_needs_id"),
        CheckConstraint(f"css_color IS NULL OR css_color GLOB '#{_HEX * 3}' "
                        f"OR css_color GLOB '#{_HEX * 6}'",
                        name="ck_annotation_term_css_color"),
    )


class Annotation(Base, TimestampMixin):
    """One tag / data attribute on exactly one specimen, event or biological association.

    ``(term_id, term_kind)`` is a composite FK onto ``annotation_term(id, kind)`` — the
    kind is repeated here so the value CHECK can see it (a tag has no value, a data
    attribute must have one)."""

    __tablename__ = "annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_object_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collection_object.id", ondelete="CASCADE"), nullable=True)
    collecting_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("collecting_event.id", ondelete="CASCADE"), nullable=True)
    biological_association_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("biological_association.id", ondelete="CASCADE"), nullable=True)
    term_id: Mapped[int] = mapped_column(Integer, nullable=False)
    term_kind: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    term: Mapped[AnnotationTerm] = relationship(AnnotationTerm, lazy="joined")

    __table_args__ = (
        ForeignKeyConstraint(["term_id", "term_kind"],
                             ["annotation_term.id", "annotation_term.kind"],
                             ondelete="RESTRICT", name="fk_annotation_term"),
        CheckConstraint("((collection_object_id IS NOT NULL) + (collecting_event_id IS NOT "
                        "NULL) + (biological_association_id IS NOT NULL)) = 1",
                        name="ck_annotation_exclusive_arc"),
        CheckConstraint("term_kind IN ('tag', 'data_attribute')", name="ck_annotation_kind"),
        CheckConstraint("(term_kind = 'tag' AND value IS NULL) OR (term_kind = "
                        "'data_attribute' AND value IS NOT NULL AND length(trim(value)) > 0 "
                        "AND " + _CLEAN.format(c="value") + ")",
                        name="ck_annotation_value_by_kind"),
        Index("uq_annotation_specimen_term", "collection_object_id", "term_id", unique=True,
              sqlite_where=text("collection_object_id IS NOT NULL")),
        Index("uq_annotation_event_term", "collecting_event_id", "term_id", unique=True,
              sqlite_where=text("collecting_event_id IS NOT NULL")),
        Index("uq_annotation_association_term", "biological_association_id", "term_id",
              unique=True, sqlite_where=text("biological_association_id IS NOT NULL")),
    )
```

In `app/models/biological.py`: change the import line to
`from sqlalchemy import Integer, String, ForeignKey, CheckConstraint` (unchanged) plus
`from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym`, and replace the
body of `BiologicalRelationship` after `taxonworks_id`:

```python
    taxonworks_id: Mapped[Optional[int]] = mapped_column(
        "taxonworksID", Integer, nullable=True
    )
    # The optional TaxonWorks link (migration 0073), same rules as annotation_term:
    # tw_name = TW's name as last fetched; tw_host = confirmed on that host (NULL = not
    # verified). The tw_name <-> taxonworksID coupling is two triggers (see 0073).
    tw_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tw_host: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # One name for the link id across both linkable vocabularies (tw_links.py).
    tw_id = synonym("taxonworks_id")

    associations: Mapped[List["BiologicalAssociation"]] = relationship(
        "BiologicalAssociation", back_populates="biological_relationship"
    )

    __table_args__ = (
        CheckConstraint(
            "tw_name IS NULL OR (length(trim(tw_name)) > 0 AND instr(tw_name, char(9)) = 0 "
            "AND instr(tw_name, char(10)) = 0 AND instr(tw_name, char(13)) = 0)",
            name="ck_br_tw_name_clean"),
        CheckConstraint('tw_host IS NULL OR "taxonworksID" IS NOT NULL',
                        name="ck_br_tw_host_needs_id"),
    )
```

Also update its docstring: replace the sentence "taxonworksID stores the TW internal ID for
the relationship type, used when creating biological associations via the TW API." with
"taxonworksID is the optional TaxonWorks link id (see tw_links.py); it is set only by an
explicit link in the Controlled Vocabularies tab."

In `app/models/__init__.py` add `from .annotation import AnnotationTerm, Annotation` after
the `ImportDataset` import, and `"AnnotationTerm", "Annotation",` to `__all__`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_schema.py tests/test_schema_integrity.py`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS (no other test touches these tables).

- [ ] **Step 7: Document the schema**

`docs/schema.html`:
- In the Mermaid ER block, extend `biological_relationship { … }` with
  `TEXT tw_name` and `TEXT tw_host`, and add after `external_identifier { … }`:

```
    annotation_term {
        INTEGER id PK
        TEXT kind "tag | data_attribute"
        TEXT name
        INTEGER tw_id "optional TW link"
        TEXT tw_name
        TEXT tw_host
        TEXT css_color
    }

    annotation {
        INTEGER id PK
        INTEGER collection_object_id FK "arc: exactly one"
        INTEGER collecting_event_id FK "arc: exactly one"
        INTEGER biological_association_id FK "arc: exactly one"
        INTEGER term_id FK
        TEXT term_kind FK
        TEXT value
    }
```
  and the relations
  `annotation_term ||--o{ annotation : "term of"`,
  `collection_object |o--o{ annotation : "annotates (arc)"`,
  `collecting_event |o--o{ annotation : "annotates (arc)"`,
  `biological_association |o--o{ annotation : "annotates (arc)"`.
- In the `biological_relationship` table card: change the `taxonworksID` note to
  "optional TaxonWorks link id; partial UNIQUE where set (0073)" and add two rows
  `tw_name` ("TW name as last fetched; set exactly when taxonworksID is set — triggers
  trg_br_tw_link_ins/_upd; no tab/CR/LF") and `tw_host` ("host the link was confirmed on;
  NULL = not verified; needs taxonworksID"). Replace the description's last sentence with
  "The optional TaxonWorks link is set only through the Controlled Vocabularies tab
  (migration 0073; the old launch sync is gone)."
- Add two table cards after `external_identifier`, in its exact HTML style:
  `annotation_term` (all ten columns + timestamps; notes: kind CHECK IN (tag,
  data_attribute); name NN, UNIQUE(kind, name); allow_* INTEGER 0/1; tw_id UNIQUE(kind,
  tw_id); tw_name NOT NULL iff tw_id, no tab/CR/LF; tw_host needs tw_id; css_color CHECK
  NULL or #rgb/#rrggbb; UNIQUE(id, kind) is the composite-FK target. Description: "Local
  controlled vocabulary of tags and data-attribute terms, each with an optional link to one
  TaxonWorks keyword / predicate. Migration 0073.") and `annotation` (columns; arc CHECK
  ck_annotation_exclusive_arc, FKs ON DELETE CASCADE; composite FK (term_id, term_kind) →
  annotation_term(id, kind) ON DELETE RESTRICT; ck_annotation_value_by_kind; one per
  (record, term) by three partial UNIQUE indexes uq_annotation_{specimen,event,association}_term).
- In "UNIQUE & foreign-key rules" add rows for `uq_br_taxonworks_id`, the three
  `uq_annotation_*_term` indexes, `UNIQUE(kind, tw_id)` and the composite FK.
- Add both tables to the anchor index next to `biological_relationship`.

`docs/data_model.md`: in the core-tables table, after `external_identifier`, add rows

```
| `annotation_term` | Local controlled vocabulary of **tags** and **data-attribute** terms (`kind`), each optionally linked to ONE TaxonWorks keyword / predicate (`tw_id`, `tw_name`, `tw_host` — NULL host = not verified) and with an optional chip colour `css_color` (mirrored from TW while linked). `allow_specimen/event/association` filter only what the picker offers. Migration 0073; design in `docs/superpowers/specs/2026-10-14-annotations-design.md`. |
| `annotation` | One tag / data attribute on exactly one specimen, collecting event or biological association (exclusive arc, CASCADE). Composite FK `(term_id, term_kind)` → `annotation_term(id, kind)`: a tag has no value, a data attribute must have one; one per (record, term). Migration 0073. |
```

and change the `biological_relationship` row to: "Kind of association (`collected_on`,
`feeds_on`, …). Optional TaxonWorks link `taxonworksID` + `tw_name` + `tw_host`, same
rules as `annotation_term` (0073)."

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/0073_annotations.py app/models/annotation.py app/models/biological.py \
  app/models/__init__.py tests/test_annotation_schema.py tests/test_schema_integrity.py \
  docs/schema.html docs/data_model.md
git commit -m "Annotations: schema (migration 0073) — annotation_term, annotation, relationship link columns

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then tell the user: migration 0073 now exists on `feature/annotations`; launching the app
from this checkout applies it to the live DB (a snapshot is taken first).

---
### Task 2: Annotation service — terms, colours, values, annotations, merge

**Files:**
- Create: `app/services/annotations.py`
- Create: `tests/annotation_helpers.py`
- Create: `tests/test_annotations.py`

**Interfaces:**
- Consumes: `AnnotationTerm`, `Annotation` (Task 1).
- Produces (used by Tasks 3, 5, 7, 8, 9, 10):
  - `KINDS = ("tag", "data_attribute")`, `TARGETS = {"specimen": "collection_object_id",
    "event": "collecting_event_id", "association": "biological_association_id"}`
  - `@dataclass(frozen=True) AnnotationItem(term_id: int, value: str | None = None)`
  - `@dataclass(frozen=True) AnnotationView(term_id: int, kind: str, name: str,
    value: str | None, css_color: str | None, linked: bool)`
  - `@dataclass(frozen=True) MergePlan(keep_name: str, absorb_name: str, repoint: int,
    fold: int, conflicts: tuple[str, ...])`
  - `normalize_css_color(value: str | None) -> str | None` (raises `ValueError`)
  - `tw_css_color(value: object) -> tuple[str | None, str | None]` → (colour, warning)
  - `clean_value(value: str | None) -> str` (raises `ValueError`)
  - `create_term(session, *, kind, name, allow_specimen=False, allow_event=False,
    allow_association=False, css_color=None) -> AnnotationTerm`
  - `update_term(session, term_id, *, name=_UNSET, kind=_UNSET, allow_specimen=_UNSET,
    allow_event=_UNSET, allow_association=_UNSET, css_color=_UNSET) -> AnnotationTerm`
  - `delete_term(session, term_id) -> None`, `term_usage(session, term_id) -> int`
  - `merge_preview(session, keep_id, absorb_id) -> MergePlan`,
    `merge_terms(session, keep_id, absorb_id) -> MergePlan`
  - `list_terms(session, kind=None) -> list[AnnotationTerm]`,
    `allowed_terms(session, target) -> list[AnnotationTerm]`
  - `annotations_for(session, target, target_id) -> list[Annotation]`,
    `views_for(session, target, target_id) -> list[AnnotationView]`
  - `set_annotations(session, target, target_id, items) -> None` (replace — Records)
  - `add_annotations(session, target, target_id, items) -> None` (additive — Digitize)
  - `name_taken(session, kind, name, exclude_id=None) -> bool`

- [ ] **Step 1: Write the shared test helpers**

Create `tests/annotation_helpers.py`:

```python
"""Fixtures shared by the annotation tests (service, export, compare)."""
from app.models import AnnotationTerm, CollectingEvent, Taxon
from app.models.base import _utcnow
from app.services import specimens as spec_svc
from app.services.taxa import compose_scientific_name
from tests.helpers import ensure_repo

SFG = "sfg.taxonworks.org"
SANDBOX = "sandbox.taxonworks.org"


def make_term(session, kind: str = "tag", name: str = "Adventive", *,
              specimen: bool = True, event: bool = False, association: bool = False,
              tw_id: int | None = None, tw_name: str | None = None,
              tw_host: str | None = None, css_color: str | None = None) -> AnnotationTerm:
    """Direct ORM insert, so a test can put a term in any link state (linked, verified,
    verified elsewhere) without going through tw_links."""
    t = AnnotationTerm(kind=kind, name=name, allow_specimen=int(specimen),
                       allow_event=int(event), allow_association=int(association),
                       tw_id=tw_id, tw_name=tw_name if tw_id is not None else None,
                       tw_host=tw_host, css_color=css_color,
                       created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def species(session) -> Taxon:
    sp = session.query(Taxon).filter_by(scientific_name="Otiorhynchus crypticus").first()
    if sp is not None:
        return sp
    genus = Taxon(name_element="Otiorhynchus", scientific_name="Otiorhynchus",
                  taxon_rank="genus", nomenclatural_code="ICZN",
                  created_at=_utcnow(), updated_at=_utcnow())
    session.add(genus)
    session.flush()
    sp = Taxon(name_element="crypticus", scientific_name="crypticus", taxon_rank="species",
               parent_name_usage_id=genus.id, nomenclatural_code="ICZN",
               created_at=_utcnow(), updated_at=_utcnow())
    session.add(sp)
    session.flush()
    sp.scientific_name = compose_scientific_name(session, sp)
    session.flush()
    return sp


def make_event(session) -> CollectingEvent:
    ev = CollectingEvent(created_at=_utcnow(), updated_at=_utcnow())
    session.add(ev)
    session.flush()
    return ev


def make_specimen(session, catalog_number: str, *, event_id: int | None = None,
                  code: str = "ANNX"):
    """An export-ELIGIBLE specimen: a definite species determination and a collection
    with both codes, so the occurrenceID triplet forms."""
    repo_id = ensure_repo(session, code, institution="JJ")
    co = spec_svc.create_collection_object(
        session, collecting_event_id=event_id, catalog_number=catalog_number,
        repository_id=repo_id)
    session.flush()
    spec_svc.create_determination(session, collection_object_id=co.id,
                                  taxon_id=species(session).id, is_current=1)
    session.flush()
    session.refresh(co)
    return co
```

- [ ] **Step 2: Write the failing service tests**

Create `tests/test_annotations.py`:

```python
"""annotations.py — the local vocabulary and the annotations on records (spec: Data model,
Service layer, Merge rules, Staged annotations)."""
import re

import pytest

import app.services.annotations as ann
from app.services.annotations import AnnotationItem
from tests.annotation_helpers import SFG, make_event, make_specimen, make_term


# ── colour and value validation ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("#abc", "#abc"), (" #AABBCC ", "#AABBCC"), ("", None), (None, None), ("  ", None)])
def test_normalize_css_color_accepts(raw, expected):
    assert ann.normalize_css_color(raw) == expected


@pytest.mark.parametrize("raw", ["red", "#abcd", "abc", "#12345", "#ggg"])
def test_normalize_css_color_refuses_and_names_the_value(raw):
    with pytest.raises(ValueError, match=re.escape(raw)):
        ann.normalize_css_color(raw)


@pytest.mark.parametrize("raw,colour,warns", [
    ("#254d0d", "#254d0d", False), (None, None, False), ("", None, False),
    ("red", None, True), ("#12345", None, True), (12, None, True)])
def test_tw_css_color_never_converts(raw, colour, warns):
    got, warning = ann.tw_css_color(raw)
    assert got == colour
    assert (warning is not None) is warns


def test_clean_value():
    assert ann.clean_value("  G1.6 ") == "G1.6"
    for bad in ("", "   ", None, "a\tb", "a\nb", "a\rb"):
        with pytest.raises(ValueError):
            ann.clean_value(bad)


# ── terms ──────────────────────────────────────────────────────────────────────

def test_create_term_refuses_case_insensitive_duplicate(session):
    ann.create_term(session, kind="tag", name="Adventive")
    with pytest.raises(ValueError, match="already exists"):
        ann.create_term(session, kind="tag", name="adventive")
    ann.create_term(session, kind="data_attribute", name="adventive")   # other kind: fine


def test_create_term_refuses_unknown_kind(session):
    with pytest.raises(ValueError):
        ann.create_term(session, kind="note", name="x")


def test_local_colour_refused_while_linked(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG, css_color="#ff8800")
    with pytest.raises(ValueError, match="TaxonWorks"):
        ann.update_term(session, t.id, css_color="#000")
    ann.update_term(session, t.id, css_color="#ff8800")      # unchanged: not an edit


def test_kind_change_refused_while_used_or_linked(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00001")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    with pytest.raises(ValueError, match="used by 1"):
        ann.update_term(session, t.id, kind="data_attribute")
    linked = make_term(session, name="Linked", tw_id=5, tw_name="Linked")
    with pytest.raises(ValueError, match="Unlink"):
        ann.update_term(session, linked.id, kind="data_attribute")


def test_delete_refused_while_used(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00002")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    with pytest.raises(ValueError, match="still used by 1"):
        ann.delete_term(session, t.id)


# ── set (replace) and add (staged) ─────────────────────────────────────────────

def test_set_annotations_replaces_the_set(session):
    tag = make_term(session)
    da = make_term(session, "data_attribute", "EUNIS habitat")
    co = make_specimen(session, "ANNX-00003")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, " G1.6 ")])
    views = ann.views_for(session, "specimen", co.id)
    assert [(v.name, v.value) for v in views] == [("Adventive", None), ("EUNIS habitat", "G1.6")]
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(da.id, "G1.7")])
    assert [(v.name, v.value) for v in ann.views_for(session, "specimen", co.id)] == [
        ("EUNIS habitat", "G1.7")]


def test_a_new_term_must_be_allowed_but_an_existing_one_stays(session):
    t = make_term(session, specimen=True)
    co = make_specimen(session, "ANNX-00004")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    ann.update_term(session, t.id, allow_specimen=False)
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])   # kept: fine
    other = make_term(session, name="Other", specimen=False)
    with pytest.raises(ValueError, match="not offered for specimens"):
        ann.set_annotations(session, "specimen", co.id,
                            [AnnotationItem(t.id), AnnotationItem(other.id)])


def test_tag_with_value_and_duplicate_term_refused(session):
    t = make_term(session)
    co = make_specimen(session, "ANNX-00005")
    with pytest.raises(ValueError, match="no value"):
        ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id, "x")])
    with pytest.raises(ValueError, match="twice"):
        ann.set_annotations(session, "specimen", co.id,
                            [AnnotationItem(t.id), AnnotationItem(t.id)])


def test_add_annotations_is_additive_and_idempotent(session):
    tag = make_term(session, event=True)
    da = make_term(session, "data_attribute", "EUNIS habitat", event=True)
    ev = make_event(session)
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(tag.id)])
    ann.add_annotations(session, "event", ev.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "G1.6")])
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.6")])
    assert len(ann.annotations_for(session, "event", ev.id)) == 2


def test_add_annotations_conflicting_value_refuses(session):
    """Review Focus 3: a staged value on a reused event that already has another value."""
    da = make_term(session, "data_attribute", "EUNIS habitat", event=True)
    ev = make_event(session)
    ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.6")])
    with pytest.raises(ValueError) as exc:
        ann.add_annotations(session, "event", ev.id, [AnnotationItem(da.id, "G1.7")])
    assert "G1.6" in str(exc.value) and "G1.7" in str(exc.value)
    assert [a.value for a in ann.annotations_for(session, "event", ev.id)] == ["G1.6"]


# ── merge ──────────────────────────────────────────────────────────────────────

def test_merge_refuses_kind_mismatch_and_a_linked_absorbed_term(session):
    tag = make_term(session)
    da = make_term(session, "data_attribute", "Adventive")
    with pytest.raises(ValueError, match="kind"):
        ann.merge_terms(session, keep_id=tag.id, absorb_id=da.id)
    linked = make_term(session, name="Linked", tw_id=9, tw_name="Linked")
    with pytest.raises(ValueError, match="Unlink"):
        ann.merge_terms(session, keep_id=tag.id, absorb_id=linked.id)


def test_merge_folds_duplicates_and_repoints_the_rest(session):
    keep = make_term(session, name="Adventive")
    absorb = make_term(session, name="adventive (typo)")
    co1 = make_specimen(session, "ANNX-00010")
    co2 = make_specimen(session, "ANNX-00011")
    ann.set_annotations(session, "specimen", co1.id,
                        [AnnotationItem(keep.id), AnnotationItem(absorb.id)])
    ann.set_annotations(session, "specimen", co2.id, [AnnotationItem(absorb.id)])
    plan = ann.merge_terms(session, keep_id=keep.id, absorb_id=absorb.id)
    assert (plan.fold, plan.repoint, plan.conflicts) == (1, 1, ())
    assert [v.name for v in ann.views_for(session, "specimen", co1.id)] == ["Adventive"]
    assert [v.name for v in ann.views_for(session, "specimen", co2.id)] == ["Adventive"]
    assert all(t.id != absorb.id for t in ann.list_terms(session))


def test_merge_refuses_conflicting_values(session):
    """Review Focus 2: one record, both terms, different values — never pick one."""
    keep = make_term(session, "data_attribute", "EUNIS habitat")
    absorb = make_term(session, "data_attribute", "EUNIS")
    co = make_specimen(session, "ANNX-00012")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(keep.id, "G1.6"), AnnotationItem(absorb.id, "G1.7")])
    preview = ann.merge_preview(session, keep_id=keep.id, absorb_id=absorb.id)
    assert preview.conflicts == (f"specimen #{co.id}: 'G1.6' vs 'G1.7'",)
    with pytest.raises(ValueError, match=f"specimen #{co.id}"):
        ann.merge_terms(session, keep_id=keep.id, absorb_id=absorb.id)


def test_views_report_colour_and_link(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG, css_color="#ff8800")
    co = make_specimen(session, "ANNX-00013")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    (v,) = ann.views_for(session, "specimen", co.id)
    assert (v.css_color, v.linked) == ("#ff8800", True)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_annotations.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.annotations'`.

- [ ] **Step 4: Write the service**

Create `app/services/annotations.py`:

```python
"""Annotations — the local tag / data-attribute vocabulary and the annotations on records.

Terms are LOCAL (``annotation_term``); a term may carry an optional link to one
TaxonWorks keyword / predicate, which ``tw_links.py`` owns — this module never talks to
TaxonWorks. Everything here refuses loudly instead of guessing (CLAUDE.md §2): a term not
offered for a record type, a tag with a value, a merge that would have to pick between two
values, a staged value that contradicts the stored one.

Two write modes, matching the two UIs (spec "Entering annotations"):
* ``set_annotations`` — REPLACE a record's set (Records' deferred Save).
* ``add_annotations`` — ADD to it (Digitize's staged Save, which may land on a reused
  event). The same value is a no-op; a different value refuses.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Annotation, AnnotationTerm
from app.models.base import _utcnow

KINDS = ("tag", "data_attribute")
TARGETS = {
    "specimen": "collection_object_id",
    "event": "collecting_event_id",
    "association": "biological_association_id",
}
_ALLOW = {"specimen": "allow_specimen", "event": "allow_event",
          "association": "allow_association"}
_NOUN = {"specimen": "specimen", "event": "collecting event",
         "association": "biological association"}
_KIND_NOUN = {"tag": "tag", "data_attribute": "data attribute"}
_HEX_RE = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
_FORBIDDEN = ("\t", "\n", "\r")
_UNSET = object()


@dataclass(frozen=True)
class AnnotationItem:
    term_id: int
    value: str | None = None


@dataclass(frozen=True)
class AnnotationView:
    term_id: int
    kind: str
    name: str
    value: str | None
    css_color: str | None
    linked: bool


@dataclass(frozen=True)
class MergePlan:
    keep_name: str
    absorb_name: str
    repoint: int                 # annotations moved onto the kept term
    fold: int                    # duplicates on the same record, dropped (same meaning)
    conflicts: tuple[str, ...]   # same record, different values — the merge refuses


# ── validation ────────────────────────────────────────────────────────────────

def normalize_css_color(value: str | None) -> str | None:
    """A colour the user typed: blank -> None, ``#rgb`` / ``#rrggbb`` kept as typed,
    anything else refused with the value named. No colour picker (spec "Term colour")."""
    clean = (value or "").strip()
    if not clean:
        return None
    if not _HEX_RE.fullmatch(clean):
        raise ValueError(f"Colour '{clean}' is not #rgb or #rrggbb (e.g. #254d0d).")
    return clean


def tw_css_color(value: object) -> tuple[str | None, str | None]:
    """A colour TaxonWorks returned: ``(colour, warning)``. TW does not validate its
    ``css_color`` column (``app/models/controlled_vocabulary_term.rb``, no validation on
    it @ 897f385), so it may hold ``red`` or garbage — never converted, never guessed:
    the colour becomes None and the caller shows the warning."""
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"TaxonWorks colour {value!r} not usable (only #rgb / #rrggbb)."
    clean = value.strip()
    if not clean:
        return None, None
    if _HEX_RE.fullmatch(clean):
        return clean, None
    return None, f"TaxonWorks colour '{clean}' not usable (only #rgb / #rrggbb)."


def clean_value(value: str | None) -> str:
    """A data-attribute value: trimmed, non-empty, no tab / line break (it becomes a TSV
    cell — the DB CHECK refuses the same)."""
    clean = (value or "").strip()
    if not clean:
        raise ValueError("A data attribute needs a value.")
    if any(ch in clean for ch in _FORBIDDEN):
        raise ValueError(f"Value {clean!r} contains a tab or line break.")
    return clean


def _clean_name(name: str | None) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("A term needs a name.")
    return clean


def _col(target: str) -> str:
    if target not in TARGETS:
        raise ValueError(f"unknown annotation target {target!r}")
    return TARGETS[target]


# ── terms ─────────────────────────────────────────────────────────────────────

def _term(session: Session, term_id: int) -> AnnotationTerm:
    t = session.get(AnnotationTerm, term_id)
    if t is None:
        raise ValueError(f"annotation term {term_id} not found")
    return t


def name_taken(session: Session, kind: str, name: str,
               exclude_id: int | None = None) -> bool:
    q = session.query(AnnotationTerm).filter(
        AnnotationTerm.kind == kind,
        func.lower(AnnotationTerm.name) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(AnnotationTerm.id != exclude_id)
    return session.query(q.exists()).scalar()


def create_term(session: Session, *, kind: str, name: str, allow_specimen: bool = False,
                allow_event: bool = False, allow_association: bool = False,
                css_color: str | None = None) -> AnnotationTerm:
    if kind not in KINDS:
        raise ValueError(f"unknown term kind {kind!r}")
    clean = _clean_name(name)
    if name_taken(session, kind, clean):
        raise ValueError(f"A {_KIND_NOUN[kind]} named '{clean}' already exists.")
    t = AnnotationTerm(kind=kind, name=clean, allow_specimen=int(allow_specimen),
                       allow_event=int(allow_event),
                       allow_association=int(allow_association),
                       css_color=normalize_css_color(css_color),
                       created_at=_utcnow(), updated_at=_utcnow())
    session.add(t)
    session.flush()
    return t


def term_usage(session: Session, term_id: int) -> int:
    return session.query(Annotation).filter(Annotation.term_id == term_id).count()


def update_term(session: Session, term_id: int, *, name=_UNSET, kind=_UNSET,
                allow_specimen=_UNSET, allow_event=_UNSET, allow_association=_UNSET,
                css_color=_UNSET) -> AnnotationTerm:
    t = _term(session, term_id)
    if kind is not _UNSET and kind != t.kind:
        if kind not in KINDS:
            raise ValueError(f"unknown term kind {kind!r}")
        if t.tw_id is not None:
            raise ValueError(f"Unlink '{t.name}' from TaxonWorks before changing its kind — "
                             f"its link points at a {_KIND_NOUN[t.kind]} term.")
        n = term_usage(session, t.id)
        if n:
            raise ValueError(f"'{t.name}' is used by {n} record(s); its kind cannot change.")
        t.kind = kind
    if name is not _UNSET:
        clean = _clean_name(name)
        if name_taken(session, t.kind, clean, exclude_id=t.id):
            raise ValueError(f"A {_KIND_NOUN[t.kind]} named '{clean}' already exists.")
        t.name = clean
    for attr, val in (("allow_specimen", allow_specimen), ("allow_event", allow_event),
                      ("allow_association", allow_association)):
        if val is not _UNSET:
            setattr(t, attr, int(bool(val)))
    if css_color is not _UNSET:
        new = normalize_css_color(css_color)
        if new != t.css_color:
            if t.tw_id is not None:
                raise ValueError(f"'{t.name}' takes its colour from TaxonWorks while "
                                 f"linked — unlink it to set a local colour.")
            t.css_color = new
    t.updated_at = _utcnow()
    session.flush()
    return t


def delete_term(session: Session, term_id: int) -> None:
    t = _term(session, term_id)
    n = term_usage(session, t.id)
    if n:
        raise ValueError(f"Cannot delete '{t.name}': still used by {n} record(s).")
    session.delete(t)
    session.flush()


def list_terms(session: Session, kind: str | None = None) -> list[AnnotationTerm]:
    q = session.query(AnnotationTerm)
    if kind is not None:
        q = q.filter(AnnotationTerm.kind == kind)
    return q.order_by(AnnotationTerm.kind, func.lower(AnnotationTerm.name)).all()


def allowed_terms(session: Session, target: str) -> list[AnnotationTerm]:
    _col(target)                                   # refuses an unknown target
    flag = getattr(AnnotationTerm, _ALLOW[target])
    return (session.query(AnnotationTerm).filter(flag == 1)
            .order_by(AnnotationTerm.kind, func.lower(AnnotationTerm.name)).all())


# ── merge ─────────────────────────────────────────────────────────────────────

def _record_label(a: Annotation) -> str:
    for target, col in TARGETS.items():
        rid = getattr(a, col)
        if rid is not None:
            return f"{target} #{rid}"
    return f"annotation #{a.id}"


def _plan(session: Session, keep_id: int, absorb_id: int):
    if keep_id == absorb_id:
        raise ValueError("Cannot merge a term with itself.")
    keep, absorb = _term(session, keep_id), _term(session, absorb_id)
    if keep.kind != absorb.kind:
        raise ValueError(f"'{absorb.name}' is a {_KIND_NOUN[absorb.kind]} and '{keep.name}' "
                         f"a {_KIND_NOUN[keep.kind]} — only terms of the same kind merge.")
    if absorb.tw_id is not None:
        raise ValueError(f"Unlink '{absorb.name}' from TaxonWorks first — a merge never "
                         f"discards or moves a link.")
    kept_by_record = {}
    for a in session.query(Annotation).filter(Annotation.term_id == keep.id):
        kept_by_record[_record_label(a)] = a
    fold, move, conflicts = [], [], []
    for a in session.query(Annotation).filter(Annotation.term_id == absorb.id):
        other = kept_by_record.get(_record_label(a))
        if other is None:
            move.append(a)
        elif other.value == a.value:
            fold.append(a)
        else:
            conflicts.append(f"{_record_label(a)}: {other.value!r} vs {a.value!r}")
    plan = MergePlan(keep_name=keep.name, absorb_name=absorb.name, repoint=len(move),
                     fold=len(fold), conflicts=tuple(conflicts))
    return plan, absorb, fold, move


def merge_preview(session: Session, keep_id: int, absorb_id: int) -> MergePlan:
    return _plan(session, keep_id, absorb_id)[0]


def merge_terms(session: Session, keep_id: int, absorb_id: int) -> MergePlan:
    """De-duplicate two terms. Refuses (spec "Merge rules") on a kind mismatch, a linked
    absorbed term, or any record holding both with different values."""
    plan, absorb, fold, move = _plan(session, keep_id, absorb_id)
    if plan.conflicts:
        shown = "; ".join(plan.conflicts[:5])
        more = f" (+{len(plan.conflicts) - 5} more)" if len(plan.conflicts) > 5 else ""
        raise ValueError(f"Cannot merge '{plan.absorb_name}' into '{plan.keep_name}': "
                         f"records hold different values — {shown}{more}. "
                         f"Make them agree first.")
    for a in fold:
        session.delete(a)
    session.flush()
    now = _utcnow()
    keep = _term(session, keep_id)
    for a in move:
        # Through the relationship, not term_id alone: `a.term` is loaded (lazy="joined")
        # and still points at the absorbed row, which is deleted next.
        a.term = keep
        a.updated_at = now
    session.flush()
    session.delete(absorb)
    session.flush()
    return plan


# ── annotations on records ────────────────────────────────────────────────────

def annotations_for(session: Session, target: str, target_id: int) -> list[Annotation]:
    col = getattr(Annotation, _col(target))
    return (session.query(Annotation).join(AnnotationTerm, Annotation.term_id == AnnotationTerm.id)
            .filter(col == target_id)
            .order_by(AnnotationTerm.kind, func.lower(AnnotationTerm.name)).all())


def views_for(session: Session, target: str, target_id: int) -> list[AnnotationView]:
    return [AnnotationView(term_id=a.term.id, kind=a.term.kind, name=a.term.name,
                           value=a.value, css_color=a.term.css_color,
                           linked=a.term.tw_id is not None)
            for a in annotations_for(session, target, target_id)]


def _value_for(term: AnnotationTerm, raw: str | None) -> str | None:
    if term.kind == "tag":
        if raw not in (None, ""):
            raise ValueError(f"'{term.name}' is a tag and carries no value.")
        return None
    return clean_value(raw)


def _require_allowed(term: AnnotationTerm, target: str) -> None:
    if not getattr(term, _ALLOW[target]):
        raise ValueError(f"'{term.name}' is not offered for {_NOUN[target]}s — allow it in "
                         f"Controlled Vocabularies first.")


def _unique_items(items: Iterable[AnnotationItem]) -> list[AnnotationItem]:
    out, seen = [], set()
    for it in items:
        if it.term_id in seen:
            raise ValueError(f"term {it.term_id} appears twice in one annotation set")
        seen.add(it.term_id)
        out.append(it)
    return out


def _insert(session: Session, target: str, target_id: int, term: AnnotationTerm,
            value: str | None) -> None:
    a = Annotation(term_id=term.id, term_kind=term.kind, value=value,
                   created_at=_utcnow(), updated_at=_utcnow())
    setattr(a, _col(target), target_id)
    session.add(a)


def set_annotations(session: Session, target: str, target_id: int,
                    items: Iterable[AnnotationItem]) -> None:
    """Replace the record's annotation set (inside the caller's transaction). A term
    already on the record stays even if it is no longer offered for this target; a NEW
    term must be offered (spec: "allow" switches filter the picker only)."""
    items = _unique_items(items)
    existing = {a.term_id: a for a in annotations_for(session, target, target_id)}
    wanted = {it.term_id for it in items}
    for term_id, a in existing.items():
        if term_id not in wanted:
            session.delete(a)
    session.flush()
    for it in items:
        term = _term(session, it.term_id)
        value = _value_for(term, it.value)
        a = existing.get(term.id)
        if a is not None:
            if a.value != value:
                a.value = value
                a.updated_at = _utcnow()
            continue
        _require_allowed(term, target)
        _insert(session, target, target_id, term, value)
    session.flush()


def add_annotations(session: Session, target: str, target_id: int,
                    items: Iterable[AnnotationItem]) -> None:
    """Add staged annotations (Digitize Save). Same term + same value: no-op. Same data
    attribute with another value: refused, both values named — the Save fails whole."""
    items = _unique_items(items)
    existing = {a.term_id: a for a in annotations_for(session, target, target_id)}
    for it in items:
        term = _term(session, it.term_id)
        value = _value_for(term, it.value)
        a = existing.get(term.id)
        if a is not None:
            if a.value != value:
                raise ValueError(
                    f"{_NOUN[target].capitalize()} #{target_id} already has "
                    f"'{term.name}' = '{a.value}'; the value entered here is '{value}'. "
                    f"Change it in Records instead.")
            continue
        _require_allowed(term, target)
        _insert(session, target, target_id, term, value)
    session.flush()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n collection python -m pytest -q tests/test_annotations.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/annotations.py tests/annotation_helpers.py tests/test_annotations.py
git commit -m "Annotations: service — terms, colour/value validation, set/add, merge rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: TaxonWorks links — fetch, link, unlink, verify (keywords, predicates, relationships)

**Files:**
- Modify: `app/services/taxonworks.py` (add `configured_host`, `get_with_retry`,
  `fetch_all_pages`)
- Create: `app/services/tw_links.py`
- Create: `tests/test_tw_links.py`

**Interfaces:**
- Consumes: `annotations.tw_css_color` (Task 2); `AnnotationTerm`,
  `BiologicalRelationship.tw_id/tw_name/tw_host` (Task 1).
- Produces (used by Tasks 4–8, 11):
  - `taxonworks.configured_host() -> str` — netloc of `tw_base`, `""` when unset
  - `taxonworks.get_with_retry(client, url, params) -> httpx.Response`
  - `taxonworks.fetch_all_pages(client, path, params, *, what, check_row, per_page=500)
    -> list[dict]` (de-duplicated by `id`; raises `TaxonWorksUnreachable` on any short or
    unstable read)
  - `tw_links.KEYWORD = "keyword"`, `PREDICATE = "predicate"`, `RELATIONSHIP = "relationship"`
  - `@dataclass(frozen=True) tw_links.TwTerm(kind: str, id: int, name: str,
    uri: str | None = None, css_color: object = None)`
  - `@dataclass(frozen=True) tw_links.VerifyOutcome(row_id: int, name: str, state: str,
    tw_name: str | None, color_warning: str | None)` — `state` ∈ `verified`,
    `name_differs`, `not_found`
  - `tw_links.kind_of(row) -> str`, `rows_of_kind(session, kind) -> list`,
    `linked_holder(session, kind, tw_id, exclude=None) -> row | None`
  - `async tw_links.fetch_candidates(kind) -> list[TwTerm]`
  - `tw_links.link(session, row, tw_term, host) -> str | None` (colour warning)
  - `tw_links.unlink(session, row) -> None`
  - `tw_links.link_state(row, host) -> str` — `local_only` / `verified` / `not_verified`
  - `tw_links.apply_verification(session, kind, candidates, host) -> list[VerifyOutcome]`
  - `async tw_links.verify(session, kind) -> list[VerifyOutcome]`
  - `tw_links.resolve_name_difference(session, row, choice, tw_name, host) -> None` —
    `choice` ∈ `use_tw`, `keep_local`, `unlink`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tw_links.py`:

```python
"""tw_links.py — the one link / verify mechanism (spec: "The TaxonWorks link",
"tw_links.py", "Term colour"). No network: httpx.MockTransport stands in for TaxonWorks."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.taxonworks as tw
import app.services.tw_links as links
from app.config import AppConfig
from app.models import BiologicalRelationship
from app.models.base import _utcnow
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_links import KEYWORD, PREDICATE, RELATIONSHIP, TwTerm
from tests.annotation_helpers import SANDBOX, SFG, make_term


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="tok"))


def _serve(monkeypatch, handler):
    """Route every tw_links / fetch_all_pages request through `handler(request)`."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(links, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


def _page(rows, total=None):
    headers = {"pagination-total": str(total)} if total is not None else {}
    return httpx.Response(200, json=rows, headers=headers)


def _kw(id_, name, **extra):
    return {"id": id_, "name": name, "type": "Keyword", "uri": None, "css_color": None,
            **extra}


# ── helpers in taxonworks.py ───────────────────────────────────────────────────

def test_configured_host(cfg):
    assert tw.configured_host() == SFG
    config_mod._instance.tw_base = ""
    assert tw.configured_host() == ""


def test_fetch_all_pages_repeated_rows_are_a_lookup_failure(cfg):
    """Review Focus 1: /controlled_vocabulary_terms and /data_attributes page without an
    ORDER BY, so a row can repeat on the next page while another is skipped. Counting
    received rows would call that complete; counting DISTINCT ids does not."""
    pages = {1: [_kw(1, "A"), _kw(2, "B")], 2: [_kw(2, "B"), _kw(3, "C")]}

    def handler(request):
        return _page(pages[int(request.url.params["page"])], total=4)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.fetch_all_pages(client, "/controlled_vocabulary_terms", {},
                                            what="the Keyword listing",
                                            check_row=lambda r: None, per_page=2)
    with pytest.raises(TaxonWorksUnreachable, match="3 distinct"):
        asyncio.run(run())


def test_fetch_all_pages_short_read_is_a_lookup_failure(cfg):
    def handler(request):
        page = int(request.url.params["page"])
        return _page([_kw(1, "A"), _kw(2, "B")] if page == 1 else [], total=5)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.fetch_all_pages(client, "/x", {}, what="the listing",
                                            check_row=lambda r: None, per_page=2)
    with pytest.raises(TaxonWorksUnreachable, match="2 of 5"):
        asyncio.run(run())


def test_get_with_retry_retries_a_transient_failure(cfg, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(tw, "_RETRY_BACKOFF", (0, 0))

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else _page([])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tw.get_with_retry(client, "https://x/api/v1/y", {})
    assert asyncio.run(run()).status_code == 200
    assert calls["n"] == 2


# ── fetch_candidates ───────────────────────────────────────────────────────────

def test_fetch_candidates_keywords(cfg, monkeypatch):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return _page([_kw(1764, "Adventive", css_color="#ff8800"), _kw(5, "Endemic")],
                     total=2)
    _serve(monkeypatch, handler)
    got = asyncio.run(links.fetch_candidates(KEYWORD))
    assert seen["type"] == "Keyword" and seen["project_token"] == "tok"
    assert [(t.id, t.name, t.css_color) for t in got] == [
        (1764, "Adventive", "#ff8800"), (5, "Endemic", None)]


def test_fetch_candidates_wrong_type_is_a_lookup_failure(cfg, monkeypatch):
    """Unknown params are silently ignored (§5c): an unfiltered reply mixes types."""
    _serve(monkeypatch, lambda r: _page([_kw(1, "A"), {**_kw(2, "B"), "type": "Predicate"}]))
    with pytest.raises(TaxonWorksUnreachable, match="type filter"):
        asyncio.run(links.fetch_candidates(KEYWORD))


def test_fetch_candidates_names_a_rejected_token(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(401))
    with pytest.raises(TaxonWorksUnreachable, match="rejected the project token"):
        asyncio.run(links.fetch_candidates(PREDICATE))


def test_fetch_candidates_relationships(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: _page([{"id": 11, "name": "feeds on",
                                          "inverted_name": "food of"}], total=1))
    (t,) = asyncio.run(links.fetch_candidates(RELATIONSHIP))
    assert (t.kind, t.id, t.name) == (RELATIONSHIP, 11, "feeds on")


# ── link / unlink / state ──────────────────────────────────────────────────────

def test_link_sets_id_name_host_and_colour(session):
    t = make_term(session)
    warning = links.link(session, t, TwTerm(KEYWORD, 1764, "Adventive", None, "#ff8800"), SFG)
    assert warning is None
    assert (t.tw_id, t.tw_name, t.tw_host, t.css_color) == (1764, "Adventive", SFG, "#ff8800")
    assert links.link_state(t, SFG) == "verified"
    assert links.link_state(t, SANDBOX) == "not_verified"


def test_link_refuses_an_id_held_by_another_term(session):
    make_term(session, name="First", tw_id=1764, tw_name="Adventive", tw_host=SFG)
    second = make_term(session, name="Second")
    with pytest.raises(ValueError, match="already linked to 'First'"):
        links.link(session, second, TwTerm(KEYWORD, 1764, "Adventive"), SFG)


def test_link_refuses_the_wrong_kind(session):
    t = make_term(session)                         # a tag
    with pytest.raises(ValueError):
        links.link(session, t, TwTerm(PREDICATE, 3, "Altitude"), SFG)


@pytest.mark.parametrize("raw", ["red", "#12345", 7])
def test_link_with_unusable_colour_keeps_link_and_warns(session, raw):
    """Review Focus 5."""
    t = make_term(session, css_color="#000")
    warning = links.link(session, t, TwTerm(KEYWORD, 9, "Adventive", None, raw), SFG)
    assert warning is not None and "not usable" in warning
    assert (t.tw_id, t.tw_host, t.css_color) == (9, SFG, None)


def test_unlink_keeps_the_colour(session):
    t = make_term(session, tw_id=9, tw_name="Adventive", tw_host=SFG, css_color="#254d0d")
    links.unlink(session, t)
    assert (t.tw_id, t.tw_name, t.tw_host, t.css_color) == (None, None, None, "#254d0d")
    assert links.link_state(t, SFG) == "local_only"


def test_relationship_links_through_the_same_path(session):
    r = BiologicalRelationship(name="feeds on", created_at=_utcnow(), updated_at=_utcnow())
    session.add(r)
    session.flush()
    assert links.link(session, r, TwTerm(RELATIONSHIP, 11, "feeds on"), SFG) is None
    assert (r.taxonworks_id, r.tw_name, r.tw_host) == (11, "feeds on", SFG)
    links.unlink(session, r)
    assert (r.taxonworks_id, r.tw_name, r.tw_host) == (None, None, None)


# ── verify ─────────────────────────────────────────────────────────────────────

def test_apply_verification_outcomes(session):
    same = make_term(session, name="Same", tw_id=1, tw_name="Same")
    renamed = make_term(session, name="Old", tw_id=2, tw_name="Old", tw_host=SFG,
                        css_color="#000")
    gone = make_term(session, name="Gone", tw_id=3, tw_name="Gone", tw_host=SFG)
    make_term(session, name="Local")
    out = links.apply_verification(session, KEYWORD, [
        TwTerm(KEYWORD, 1, "Same", None, "#abc"),
        TwTerm(KEYWORD, 2, "New name", None, "#fff")], SFG)
    by_name = {o.name: o for o in out}
    assert set(by_name) == {"Same", "Old", "Gone"}            # local-only never checked
    assert by_name["Same"].state == "verified" and same.tw_host == SFG
    assert same.css_color == "#abc"
    assert by_name["Old"].state == "name_differs" and by_name["Old"].tw_name == "New name"
    assert renamed.tw_host is None and renamed.tw_name == "Old"
    assert renamed.css_color == "#fff"                          # colour follows TW anyway
    assert by_name["Gone"].state == "not_found"
    assert gone.tw_host is None and gone.tw_id == 3             # link never removed


@pytest.mark.parametrize("choice,name,tw_name,host_set", [
    ("use_tw", "New name", "New name", True),
    ("keep_local", "Old", "New name", True),
    ("unlink", "Old", None, False)])
def test_resolve_name_difference(session, choice, name, tw_name, host_set):
    t = make_term(session, name="Old", tw_id=2, tw_name="Old")
    links.resolve_name_difference(session, t, choice, "New name", SFG)
    assert (t.name, t.tw_name, t.tw_host is not None) == (name, tw_name, host_set)


def test_use_tw_name_refuses_a_local_name_clash(session):
    make_term(session, name="new name")
    t = make_term(session, name="Old", tw_id=2, tw_name="Old")
    with pytest.raises(ValueError, match="already exists"):
        links.resolve_name_difference(session, t, "use_tw", "New name", SFG)


def test_verify_end_to_end(cfg, monkeypatch, session):
    t = make_term(session, tw_id=1764, tw_name="Adventive")
    _serve(monkeypatch, lambda r: _page([_kw(1764, "Adventive")], total=1))
    (o,) = asyncio.run(links.verify(session, KEYWORD))
    assert o.state == "verified" and t.tw_host == SFG
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_tw_links.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tw_links'`.

- [ ] **Step 3: Add the shared fetch helpers to `taxonworks.py`**

Append to `app/services/taxonworks.py` (after `PaginationTracker`):

```python
def configured_host() -> str:
    """The host part (netloc) of the configured API base URL, e.g. ``sfg.taxonworks.org``
    for ``https://sfg.taxonworks.org/api/v1``; ``""`` when no base URL is set.

    The one value every TaxonWorks link is scoped to (annotation terms, relationships —
    spec "The TaxonWorks link"), the same netloc the OTU provenance check compares
    (``tw_sync.otu_instance_state``). Scheme and path are irrelevant to which instance
    issued an id."""
    base = get_config().tw_base.strip().rstrip("/")
    return urlsplit(base).netloc if base else ""


_RETRY_STATUS = (429, 502, 503, 504)
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF = (0.5, 1.5)


async def get_with_retry(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """One GET, retried on a transient failure (rate limit, 502/503/504, timeout) — a
    passing hiccup on this shared public server must not read as a real failure. Any
    other error, or the last failed attempt, raises ``TaxonWorksUnreachable`` naming the
    cause (``explain_tw_error``). Same discipline as ``tw_media_compare._get_page``."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRY_STATUS:
                last_exc = exc
            else:
                raise explain_tw_error(exc) from exc
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
    raise explain_tw_error(last_exc) from last_exc


async def fetch_all_pages(client: httpx.AsyncClient, path: str, params: dict, *, what: str,
                          check_row, per_page: int = 500) -> list[dict]:
    """Every row of a paged index route, de-duplicated by ``id``.

    ``check_row(row)`` re-verifies each row against the filter that was asked for and
    raises ``TaxonWorksUnreachable`` when it does not match — unknown query params are
    silently ignored (CLAUDE.md §5c), so a result is never self-evidently filtered.

    Some index routes page **without an ORDER BY** (``data_attributes_controller.rb:50``,
    ``controlled_vocabulary_terms_controller.rb:133`` @ 897f385), so the same row can come
    back on two pages while another is skipped. Counting received rows would call that
    complete; the distinct ids are counted against ``pagination-total`` instead."""
    _require_configured()
    tracker = PaginationTracker()
    seen: dict[int, dict] = {}
    page = 1
    while True:
        r = await get_with_retry(client, f"{_base()}{path}", {
            **params, "per": per_page, "page": page, "project_token": _token()})
        body = r.json()
        if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
            raise TaxonWorksUnreachable(
                f"{what} did not return a list of rows — treated as a lookup failure, "
                f"not as an empty result.")
        for row in body:
            check_row(row)
            rid = row.get("id")
            if not isinstance(rid, int):
                raise TaxonWorksUnreachable(f"{what} returned a row without an id.")
            seen[rid] = row
        tracker.record_page(r, body)
        if tracker.is_complete(body, per_page):
            break
        page += 1
    tracker.check_complete(what=what)
    if tracker.total is not None and len(seen) < tracker.total:
        raise TaxonWorksUnreachable(
            f"{what} returned {len(seen)} distinct rows of {tracker.total} — its pages "
            f"are not stably ordered, so rows were repeated and others possibly skipped. "
            f"Treated as a lookup failure; try again.")
    return list(seen.values())
```

- [ ] **Step 4: Write `tw_links.py`**

Create `app/services/tw_links.py`:

```python
"""One link / verify mechanism for the three linkable vocabularies (spec "tw_links.py").

A local term (tag, data-attribute term) or biological relationship may carry ONE link to
a TaxonWorks keyword / predicate / biological relationship: ``tw_id`` + ``tw_name`` (TW's
name as last fetched) + ``tw_host`` (the host the link was CONFIRMED on; NULL = not
verified). The link is hard to earn and easy to lose: ``tw_host`` is written only when a
fetch from that host returned this id with this name; anything else clears it. Nothing
here runs on its own — every fetch is a user action (the launch sync is gone).

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* ``GET /api/v1/controlled_vocabulary_terms?type=Keyword|Predicate`` — paged,
  project-scoped (``config/routes/api_v1.rb:142``; ``controlled_vocabulary_terms_controller
  .rb#api_index``; ``type`` filter ``lib/queries/controlled_vocabulary_term/filter.rb:29``).
  Fields ``id, type, name, definition, uri, uri_relation, css_color``
  (``app/views/controlled_vocabulary_terms/api/v1/_attributes.json.jbuilder:1``).
* ``GET /api/v1/biological_relationships`` — paged, ordered by name
  (``biological_relationships_controller.rb#api_index:20``); fields ``id, name,
  inverted_name, …`` (``app/views/biological_relationships/api/v1/_attributes.json.jbuilder``).
  Biological relationships have no colour column (``db/schema.rb``).
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.services.taxonworks as tw
from app.models import AnnotationTerm, BiologicalRelationship
from app.models.base import _utcnow
from app.services import annotations as ann
from app.services.taxonworks import TaxonWorksUnreachable

KEYWORD = "keyword"
PREDICATE = "predicate"
RELATIONSHIP = "relationship"
_CVT_TYPE = {KEYWORD: "Keyword", PREDICATE: "Predicate"}
_TERM_KIND = {"tag": KEYWORD, "data_attribute": PREDICATE}
_KIND_TERM = {v: k for k, v in _TERM_KIND.items()}
_NOUN = {KEYWORD: "keyword", PREDICATE: "predicate", RELATIONSHIP: "relationship"}
_TIMEOUT = httpx.Timeout(25.0)


def _client() -> httpx.AsyncClient:
    """Factory, so tests can substitute an ``httpx.MockTransport``."""
    return httpx.AsyncClient(timeout=_TIMEOUT)


@dataclass(frozen=True)
class TwTerm:
    kind: str
    id: int
    name: str
    uri: str | None = None
    css_color: object = None      # raw, unvalidated — see annotations.tw_css_color


@dataclass(frozen=True)
class VerifyOutcome:
    row_id: int
    name: str                     # the local name
    state: str                    # verified | name_differs | not_found
    tw_name: str | None           # TW's current name (None when not found)
    color_warning: str | None


def kind_of(row) -> str:
    if isinstance(row, BiologicalRelationship):
        return RELATIONSHIP
    if isinstance(row, AnnotationTerm):
        return _TERM_KIND[row.kind]
    raise TypeError(f"not a linkable vocabulary row: {row!r}")


def rows_of_kind(session: Session, kind: str) -> list:
    if kind == RELATIONSHIP:
        return (session.query(BiologicalRelationship)
                .order_by(func.lower(BiologicalRelationship.name)).all())
    return ann.list_terms(session, _KIND_TERM[kind])


def linked_holder(session: Session, kind: str, tw_id: int, exclude=None):
    """The local row of this kind already linked to ``tw_id``, if any."""
    for row in rows_of_kind(session, kind):
        if row.tw_id == tw_id and row is not exclude:
            return row
    return None


def _check_named(row: dict, what: str) -> None:
    if not isinstance(row.get("id"), int) or not str(row.get("name") or "").strip():
        raise TaxonWorksUnreachable(f"{what} returned a row without an id or name.")


async def fetch_candidates(kind: str) -> list[TwTerm]:
    """The TaxonWorks list for one kind, complete or not at all."""
    if kind in _CVT_TYPE:
        expected = _CVT_TYPE[kind]
        what = f"the TaxonWorks {expected} listing"

        def check(row: dict) -> None:
            if row.get("type") != expected:
                raise TaxonWorksUnreachable(
                    f"{what} returned a {row.get('type')!r} row — the type filter did not "
                    f"apply. Treated as a lookup failure.")
            _check_named(row, what)

        async with _client() as client:
            rows = await tw.fetch_all_pages(client, "/controlled_vocabulary_terms",
                                            {"type": expected}, what=what, check_row=check)
        terms = [TwTerm(kind, r["id"], str(r["name"]).strip(), r.get("uri") or None,
                        r.get("css_color")) for r in rows]
    elif kind == RELATIONSHIP:
        what = "the TaxonWorks biological relationship listing"
        async with _client() as client:
            rows = await tw.fetch_all_pages(client, "/biological_relationships", {},
                                            what=what,
                                            check_row=lambda r: _check_named(r, what))
        terms = [TwTerm(kind, r["id"], str(r["name"]).strip()) for r in rows]
    else:
        raise ValueError(f"unknown link kind {kind!r}")
    return sorted(terms, key=lambda t: t.name.casefold())


def _require_clean_tw_name(name: str) -> None:
    if not name.strip() or any(ch in name for ch in ("\t", "\n", "\r")):
        raise ValueError(f"TaxonWorks name {name!r} contains a tab or line break and "
                         f"cannot become an export column.")


def link(session: Session, row, tw_term: TwTerm, host: str) -> str | None:
    """Link ``row`` to a term picked from a FRESH fetch, so it is verified at once.
    Returns a colour warning (annotation terms only) or None."""
    kind = kind_of(row)
    if tw_term.kind != kind:
        raise ValueError(f"cannot link a {_NOUN[kind]} to a TaxonWorks {_NOUN[tw_term.kind]}")
    if not host:
        raise ValueError("No TaxonWorks API base URL set — Settings → TaxonWorks connection.")
    _require_clean_tw_name(tw_term.name)
    holder = linked_holder(session, kind, tw_term.id, exclude=row)
    if holder is not None:
        raise ValueError(f"TaxonWorks {_NOUN[kind]} '{tw_term.name}' is already linked to "
                         f"'{holder.name}'.")
    row.tw_id = tw_term.id
    row.tw_name = tw_term.name
    row.tw_host = host
    warning = None
    if isinstance(row, AnnotationTerm):
        row.css_color, warning = ann.tw_css_color(tw_term.css_color)
    row.updated_at = _utcnow()
    session.flush()
    return warning


def unlink(session: Session, row) -> None:
    """The row becomes local only. An annotation term keeps its colour as a local one."""
    row.tw_id = None
    row.tw_name = None
    row.tw_host = None
    row.updated_at = _utcnow()
    session.flush()


def link_state(row, host: str) -> str:
    if row.tw_id is None:
        return "local_only"
    if row.tw_host and row.tw_host == host:
        return "verified"
    return "not_verified"


def apply_verification(session: Session, kind: str, candidates: list[TwTerm],
                       host: str) -> list[VerifyOutcome]:
    """Per linked row: same id + same name -> confirmed on ``host``; same id, other name
    -> unconfirmed, user decides (``resolve_name_difference``); id unknown here ->
    unconfirmed, link kept. The colour of an annotation term follows TW whenever the id
    was found (spec "Term colour")."""
    by_id = {c.id: c for c in candidates}
    out: list[VerifyOutcome] = []
    for row in rows_of_kind(session, kind):
        if row.tw_id is None:
            continue
        cand = by_id.get(row.tw_id)
        warning = None
        if cand is None:
            row.tw_host = None
            state, tw_name = "not_found", None
        else:
            if isinstance(row, AnnotationTerm):
                row.css_color, warning = ann.tw_css_color(cand.css_color)
            tw_name = cand.name
            if cand.name == row.tw_name:
                row.tw_host, state = host, "verified"
            else:
                row.tw_host, state = None, "name_differs"
        row.updated_at = _utcnow()
        out.append(VerifyOutcome(row.id, row.name, state, tw_name, warning))
    session.flush()
    return out


async def verify(session: Session, kind: str) -> list[VerifyOutcome]:
    """Only on user request: one fetch from the configured host, then
    ``apply_verification``. Raises ``TaxonWorksUnreachable`` naming the cause."""
    host = tw.configured_host()
    if not host:
        raise TaxonWorksUnreachable(
            "No TaxonWorks API base URL set — Settings → TaxonWorks connection.")
    candidates = await fetch_candidates(kind)
    return apply_verification(session, kind, candidates, host)


def _local_name_taken(session: Session, row, name: str) -> bool:
    if isinstance(row, AnnotationTerm):
        return ann.name_taken(session, row.kind, name, exclude_id=row.id)
    q = session.query(BiologicalRelationship).filter(
        func.lower(BiologicalRelationship.name) == name.strip().lower(),
        BiologicalRelationship.id != row.id)
    return session.query(q.exists()).scalar()


def resolve_name_difference(session: Session, row, choice: str, tw_name: str,
                            host: str) -> None:
    """The user's answer to ``name_differs`` — nothing is decided silently."""
    if choice == "unlink":
        unlink(session, row)
        return
    _require_clean_tw_name(tw_name)
    if choice == "use_tw":
        if _local_name_taken(session, row, tw_name):
            raise ValueError(f"A local entry named '{tw_name}' already exists — merge or "
                             f"rename it first.")
        row.name = tw_name.strip()
    elif choice != "keep_local":
        raise ValueError(f"unknown choice {choice!r}")
    row.tw_name = tw_name
    row.tw_host = host
    row.updated_at = _utcnow()
    session.flush()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `conda run -n collection python -m pytest -q tests/test_tw_links.py tests/test_taxonworks_pagination.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/taxonworks.py app/services/tw_links.py tests/test_tw_links.py
git commit -m "Annotations: tw_links — fetch/link/unlink/verify for keywords, predicates, relationships

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Relationships as a managed vocabulary; remove the launch sync

**Files:**
- Modify: `app/services/biological.py` (remove `sync_biological_relationships` + module
  docstring's session-start section; add `relationship_vocab`, `create_relationship`,
  `merge_relationships`)
- Modify: `app/services/taxonworks.py` (delete `fetch_biological_relationships`)
- Modify: `app/ui/main.py` (remove the import on line 45 and the `_bio_sync` block)
- Create: `tests/test_relationship_vocab.py`

**Interfaces:**
- Consumes: `Vocabulary` (`app/services/vocab.py`), `BiologicalRelationship.tw_id`.
- Produces (used by Task 8):
  - `biological.relationship_vocab: Vocabulary`
  - `biological.create_relationship(session, name) -> BiologicalRelationship`
  - `biological.rename_relationship(session, rel_id, name) -> BiologicalRelationship`
  - `biological.merge_relationships(session, keep_id, absorb_id) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relationship_vocab.py`:

```python
"""Biological relationships become a managed vocabulary; the launch sync is gone
(spec "Removed: the launch sync", "Merge rules")."""
from pathlib import Path

import pytest

import app.services.biological as bio
import app.services.taxonworks as tw
from app.models.base import _utcnow
from app.models import BiologicalRelationship


def _rel(session, name, **kw):
    r = BiologicalRelationship(name=name, created_at=_utcnow(), updated_at=_utcnow(), **kw)
    session.add(r)
    session.flush()
    return r


def test_launch_sync_is_gone():
    assert not hasattr(bio, "sync_biological_relationships")
    assert not hasattr(tw, "fetch_biological_relationships")
    main_src = Path("app/ui/main.py").read_text(encoding="utf-8")
    assert "sync_biological_relationships" not in main_src


def test_create_relationship_refuses_case_insensitive_duplicate(session):
    bio.create_relationship(session, "feeds on")
    with pytest.raises(ValueError, match="already exists"):
        bio.create_relationship(session, "Feeds On")


def test_rename_refuses_case_insensitive_duplicate(session):
    bio.create_relationship(session, "feeds on")
    r = bio.create_relationship(session, "eats")
    with pytest.raises(ValueError, match="already exists"):
        bio.rename_relationship(session, r.id, "FEEDS ON")
    assert bio.rename_relationship(session, r.id, "eats on").name == "eats on"


def test_merge_refuses_a_linked_absorbed_relationship(session):
    keep = _rel(session, "feeds on")
    linked = _rel(session, "eats", taxonworks_id=11, tw_name="eats")
    with pytest.raises(ValueError, match="Unlink"):
        bio.merge_relationships(session, keep_id=keep.id, absorb_id=linked.id)


def test_merge_repoints_and_deletes(session):
    keep = _rel(session, "feeds on")
    absorb = _rel(session, "feeds-on")
    bio.merge_relationships(session, keep_id=keep.id, absorb_id=absorb.id)
    assert session.get(BiologicalRelationship, absorb.id) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_relationship_vocab.py`
Expected: FAIL — `assert not hasattr(bio, "sync_biological_relationships")`.

- [ ] **Step 3: Remove the launch sync**

In `app/ui/main.py`: delete the line
`from app.services.biological import sync_biological_relationships` and the whole block

```python
    # ── Sync TW biological relationships once per session (background) ───
    async def _bio_sync():
        try:
            with _sf() as s:
                with s.begin():
                    await sync_biological_relationships(s)
        except Exception:
            pass  # TW unreachable — local rows serve as fallback

    asyncio.create_task(_bio_sync())
```

Then check `asyncio` is still used in `main.py` (`grep -n "asyncio\." app/ui/main.py`); if
not, remove its import too.

In `app/services/taxonworks.py`: delete `fetch_biological_relationships` (its only caller
was the sync; `tw_links.fetch_candidates(RELATIONSHIP)` replaces it).

In `app/services/biological.py`: replace the module docstring with

```python
"""Biological association CRUD and the biological-relationship vocabulary.

Relationships are a local controlled vocabulary with an optional TaxonWorks link
(``tw_links.py``), managed in Controlled Vocabularies → Biological relationships. The old
launch sync is gone (spec 2026-10-14 "Removed: the launch sync"): it renamed local rows to
the TW name on every launch — after a host change that could re-label a relationship with
a *different* TW relationship's name, changing what existing associations say — and it
swallowed every error.
"""
```

delete `sync_biological_relationships` and the "Relationship sync" banner, and add after
the imports (add `from sqlalchemy import func` and `from app.services.vocab import
Vocabulary`):

```python
relationship_vocab = Vocabulary(
    BiologicalRelationship, ref_table="biological_relationship", noun="relationship")


def _clean_relationship_name(session: Session, name: str,
                             exclude_id: int | None = None) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("A relationship needs a name.")
    q = session.query(BiologicalRelationship).filter(
        func.lower(BiologicalRelationship.name) == clean.lower())
    if exclude_id is not None:
        q = q.filter(BiologicalRelationship.id != exclude_id)
    if session.query(q.exists()).scalar():
        raise ValueError(f"A relationship named '{clean}' already exists.")
    return clean


def create_relationship(session: Session, name: str) -> BiologicalRelationship:
    return relationship_vocab.create(session, name=_clean_relationship_name(session, name))


def rename_relationship(session: Session, rel_id: int, name: str) -> BiologicalRelationship:
    """Rename the LOCAL name. A link's ``tw_name`` is TaxonWorks' and is not touched — the
    two may differ by design (spec "keep local name")."""
    return relationship_vocab.update(
        session, rel_id, name=_clean_relationship_name(session, name, exclude_id=rel_id))


def merge_relationships(session: Session, keep_id: int, absorb_id: int) -> None:
    """De-duplicate two relationships (spec "Merge rules"): the absorbed one must be
    local only — a merge never discards or moves a TaxonWorks link."""
    absorb = session.get(BiologicalRelationship, absorb_id)
    if absorb is None:
        raise ValueError("relationship not found")
    if absorb.tw_id is not None:
        raise ValueError(f"Unlink '{absorb.name}' from TaxonWorks first — a merge never "
                         f"discards or moves a link.")
    relationship_vocab.merge(session, keep_id=keep_id, absorb_id=absorb_id)
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS. Then `grep -rn "sync_biological_relationships\|fetch_biological_relationships" app tests docs/*.md`
— expected: only the new test's string literals.

- [ ] **Step 5: Commit**

```bash
git add app/services/biological.py app/services/taxonworks.py app/ui/main.py \
  tests/test_relationship_vocab.py
git commit -m "Relationships: managed vocabulary with merge guard; remove the silent launch sync

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Export — `TW:Tag` / `TW:DataAttribute` columns

**Files:**
- Create: `app/services/annotation_export.py` (columns + cells; the pre-flight is Task 6)
- Modify: `app/services/dwc_export.py` (`ExportResult`, `_write_tsv`, `export_occurrences`)
- Create: `tests/test_annotation_export.py`
- Modify: `docs/taxonworks.md` (§5c "The emitted file"), `CLAUDE.md` (§5c last bullet)

**Interfaces:**
- Consumes: `Annotation`, `AnnotationTerm` (Task 1); `taxonworks.configured_host` (Task 3).
- Produces (used by Tasks 6, 11):
  - `@dataclass(frozen=True) annotation_export.ExportTerm(term_id: int, kind: str,
    tw_class: str, tw_id: int, tw_name: str, local_name: str)` with properties
    `header -> str` and `link_kind -> str` (`"keyword"` / `"predicate"`)
  - `@dataclass(frozen=True) annotation_export.AnnotationPlan(columns: tuple[ExportTerm, ...],
    cells: dict[int, dict[str, str]], blocked: tuple[tuple[str, str], ...],
    local_only: tuple[tuple[str, int], ...])`
  - `annotation_export.annotation_columns(session, cos, *, host) -> AnnotationPlan`
  - `dwc_export.ExportResult` gains `annotation_columns: tuple = ()`,
    `annotation_blocked: tuple[tuple[str, str], ...] = ()`,
    `annotation_local_only: tuple[tuple[str, int], ...] = ()`
  - `dwc_export.export_occurrences(session, cos, *, host: str | None = None)` — `tsv` is
    `""` whenever `annotation_blocked` is non-empty

- [ ] **Step 1: Write the failing tests**

Create `tests/test_annotation_export.py`:

```python
"""Export of annotations (spec "Export"): only linked + verified terms the batch uses,
header = tw_name, event annotations on every row of that event, local-only listed, an
unconfirmed link blocks, association annotations never exported."""
import csv
import io

from app.models import BiologicalRelationship
from app.models.base import _utcnow
from app.services import annotations as ann
from app.services import biological as bio
from app.services.annotations import AnnotationItem
from app.services.dwc_export import DWC_COLUMNS, export_occurrences
from tests.annotation_helpers import (SANDBOX, SFG, make_event, make_specimen, make_term,
                                      species)


def _rows(tsv: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))


def _tagged(session, cat, term, **kw):
    co = make_specimen(session, cat, **kw)
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(term.id)])
    return co


def test_no_annotations_leaves_the_header_unchanged(session):
    co = make_specimen(session, "ANNX-00100")
    result = export_occurrences(session, [co], host=SFG)
    assert result.tsv.splitlines()[0].split("\t") == list(DWC_COLUMNS)
    assert result.annotation_columns == ()


def test_specimen_tag_uses_the_tw_name(session):
    t = make_term(session, name="adventive (local)", tw_id=1764, tw_name="Adventive",
                  tw_host=SFG)
    a = _tagged(session, "ANNX-00101", t)
    b = make_specimen(session, "ANNX-00102")
    rows = _rows(export_occurrences(session, [a, b], host=SFG).tsv)
    col = "TW:Tag:CollectionObject:Adventive"
    assert [r[col] for r in rows] == ["true", ""]
    assert not any("adventive (local)" in k for k in rows[0])


def test_event_data_attribute_repeats_on_every_row_of_the_event(session):
    t = make_term(session, "data_attribute", "EUNIS habitat", event=True, tw_id=3,
                  tw_name="EUNIS habitat", tw_host=SFG)
    ev = make_event(session)
    ann.set_annotations(session, "event", ev.id, [AnnotationItem(t.id, "G1.6")])
    cos = [make_specimen(session, f"ANNX-0011{i}", event_id=ev.id) for i in range(2)]
    rows = _rows(export_occurrences(session, cos, host=SFG).tsv)
    assert [r["TW:DataAttribute:CollectingEvent:EUNIS habitat"] for r in rows] == ["G1.6"] * 2


def test_only_used_terms_become_columns(session):
    make_term(session, name="Unused", tw_id=9, tw_name="Unused", tw_host=SFG)
    co = make_specimen(session, "ANNX-00120")
    assert export_occurrences(session, [co], host=SFG).annotation_columns == ()


def test_local_only_terms_are_listed_not_emitted(session):
    t = make_term(session, name="Needs re-mount")
    co = _tagged(session, "ANNX-00121", t)
    result = export_occurrences(session, [co], host=SFG)
    assert result.annotation_local_only == (("Needs re-mount", 1),)
    assert "Needs re-mount" not in result.tsv


def test_unverified_link_blocks_and_writes_no_file(session):
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=None)
    co = _tagged(session, "ANNX-00122", t)
    result = export_occurrences(session, [co], host=SFG)
    assert result.tsv == ""
    assert result.annotation_blocked[0][0] == "Adventive"
    assert "not verified" in result.annotation_blocked[0][1]


def test_term_verified_on_another_host_blocks(session):
    """Review Focus 4: the id belongs to sfg; the configured instance is the sandbox."""
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG)
    co = _tagged(session, "ANNX-00123", t)
    result = export_occurrences(session, [co], host=SANDBOX)
    assert result.tsv == ""
    assert SFG in result.annotation_blocked[0][1] and SANDBOX in result.annotation_blocked[0][1]


def test_association_annotations_are_never_exported(session):
    t = make_term(session, specimen=False, association=True, tw_id=5, tw_name="Reared",
                  tw_host=SFG)
    co = make_specimen(session, "ANNX-00124")
    rel = BiologicalRelationship(name="feeds on", created_at=_utcnow(), updated_at=_utcnow())
    session.add(rel)
    session.flush()
    ba = bio.save_biological_association(session, collection_object_id=co.id,
                                         biological_relationship_id=rel.id,
                                         object_taxon_id=species(session).id)
    ann.set_annotations(session, "association", ba.id, [AnnotationItem(t.id)])
    result = export_occurrences(session, [co], host=SFG)
    assert result.annotation_columns == () and "Reared" not in result.tsv


def test_column_order_is_stable(session):
    tag = make_term(session, name="B tag", tw_id=1, tw_name="B tag", tw_host=SFG)
    tag2 = make_term(session, name="A tag", tw_id=2, tw_name="A tag", tw_host=SFG)
    da = make_term(session, "data_attribute", "Altitude", tw_id=3, tw_name="Altitude",
                   tw_host=SFG)
    co = make_specimen(session, "ANNX-00125")
    ann.set_annotations(session, "specimen", co.id, [
        AnnotationItem(tag.id), AnnotationItem(tag2.id), AnnotationItem(da.id, "350 m")])
    headers = [c.header for c in export_occurrences(session, [co], host=SFG).annotation_columns]
    assert headers == ["TW:Tag:CollectionObject:A tag", "TW:Tag:CollectionObject:B tag",
                       "TW:DataAttribute:CollectionObject:Altitude"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_export.py`
Expected: FAIL — `TypeError: export_occurrences() got an unexpected keyword argument 'host'`.

- [ ] **Step 3: Write `annotation_export.py`**

Create `app/services/annotation_export.py`:

```python
"""Annotation columns of the TaxonWorks occurrence export (spec "Export").

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* Headers ``TW:Tag:<Class>:<selector>`` and ``TW:DataAttribute:<Class>:<selector>`` with
  ``<Class>`` ∈ CollectionObject, CollectingEvent —
  ``app/models/dataset_record/darwin_core.rb:22`` (data attribute), ``:32`` (tag).
* Tag cell ``true`` / ``1`` applies, blank / ``false`` / ``0`` does not, anything else
  fails the row — ``occurrence.rb:1672``. Data attribute: blank cell skipped, otherwise an
  ``InternalAttribute`` with the cell as value — ``occurrence.rb:1695``.
* Event annotations are repeated on every row of the event. Safe only while rows never
  make TW reuse an existing event (we emit no ``eventID``): on reuse TW adds only new
  event TAGS and drops event DATA ATTRIBUTES silently (``occurrence.rb:469``).

A linked term means "this should reach TaxonWorks", so an unconfirmed link BLOCKS the
export (never a silent omission). Local-only terms are listed for information.
Association annotations are never exported (associations cannot be imported at all).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Annotation, CollectionObject

_PREFIX = {"tag": "Tag", "data_attribute": "DataAttribute"}
_CLASS_ORDER = {"CollectionObject": 0, "CollectingEvent": 1}
_KIND_ORDER = {"tag": 0, "data_attribute": 1}


@dataclass(frozen=True)
class ExportTerm:
    term_id: int
    kind: str           # tag | data_attribute
    tw_class: str       # CollectionObject | CollectingEvent
    tw_id: int
    tw_name: str
    local_name: str

    @property
    def header(self) -> str:
        return f"TW:{_PREFIX[self.kind]}:{self.tw_class}:{self.tw_name}"

    @property
    def link_kind(self) -> str:
        return "keyword" if self.kind == "tag" else "predicate"


@dataclass(frozen=True)
class AnnotationPlan:
    columns: tuple[ExportTerm, ...]
    cells: dict[int, dict[str, str]]          # collection_object.id -> {header: cell}
    blocked: tuple[tuple[str, str], ...]      # (local term name, reason)
    local_only: tuple[tuple[str, int], ...]   # (local term name, specimens carrying it)


def _unverified_reason(term, host: str) -> str:
    if not host:
        return "no TaxonWorks instance is configured, so its link cannot be confirmed"
    if term.tw_host is None:
        return (f"its TaxonWorks link is not verified on {host} — run Verify in "
                f"Controlled Vocabularies → Tags & data attributes")
    return (f"its TaxonWorks link was verified on {term.tw_host}, not on {host} — the id "
            f"may name something else here; run Verify again")


def annotation_columns(session: Session, cos: Iterable[CollectionObject], *,
                       host: str) -> AnnotationPlan:
    cos = list(cos)
    co_ids = [co.id for co in cos]
    event_of = {co.id: co.collecting_event_id for co in cos}
    event_ids = sorted({e for e in event_of.values() if e is not None})

    per_co: dict[int, list[tuple[str, Annotation]]] = defaultdict(list)
    if co_ids:
        for a in session.query(Annotation).filter(
                Annotation.collection_object_id.in_(co_ids)):
            per_co[a.collection_object_id].append(("CollectionObject", a))
    by_event: dict[int, list[Annotation]] = defaultdict(list)
    if event_ids:
        for a in session.query(Annotation).filter(
                Annotation.collecting_event_id.in_(event_ids)):
            by_event[a.collecting_event_id].append(a)
    for co in cos:
        for a in by_event.get(event_of[co.id], ()):
            per_co[co.id].append(("CollectingEvent", a))

    columns: dict[tuple[int, str], ExportTerm] = {}
    blocked: dict[str, str] = {}
    local: dict[str, set[int]] = defaultdict(set)
    for co_id, items in per_co.items():
        for tw_class, a in items:
            t = a.term
            if t.tw_id is None:
                local[t.name].add(co_id)
            elif not host or t.tw_host != host:
                blocked[t.name] = _unverified_reason(t, host)
            else:
                columns.setdefault((t.id, tw_class), ExportTerm(
                    term_id=t.id, kind=t.kind, tw_class=tw_class, tw_id=t.tw_id,
                    tw_name=t.tw_name, local_name=t.name))

    by_header: dict[str, list[ExportTerm]] = defaultdict(list)
    for c in columns.values():
        by_header[c.header].append(c)
    for header, same in by_header.items():
        if len(same) > 1:
            for c in same:
                blocked[c.local_name] = (f"shares the export column {header!r} with another "
                                         f"linked term")

    ordered = tuple(sorted(columns.values(), key=lambda c: (
        _CLASS_ORDER[c.tw_class], _KIND_ORDER[c.kind], c.tw_name.casefold())))
    cells: dict[int, dict[str, str]] = {}
    for co_id, items in per_co.items():
        row: dict[str, str] = {}
        for tw_class, a in items:
            c = columns.get((a.term_id, tw_class))
            if c is not None:
                row[c.header] = "true" if a.term_kind == "tag" else (a.value or "")
        cells[co_id] = row
    return AnnotationPlan(
        columns=ordered, cells=cells, blocked=tuple(sorted(blocked.items())),
        local_only=tuple(sorted((name, len(ids)) for name, ids in local.items())))
```

- [ ] **Step 4: Wire it into `dwc_export.py`**

Add imports at the top of `app/services/dwc_export.py`:
`from app.services import annotation_export` and `from app.services import taxonworks as tw_svc`.

Append three fields to `ExportResult` (after `row_count: int`):

```python
    # Annotation columns (spec 2026-10-14 "Export"): the TW:Tag / TW:DataAttribute
    # columns this batch emits, the linked terms that BLOCK it (unconfirmed link — the
    # file is then empty: a linked term means "this should reach TaxonWorks", and a link
    # that cannot be confirmed is a blocker, never a silent omission), and the local-only
    # terms the rows carry (information only, never exported).
    annotation_columns: tuple = ()
    annotation_blocked: tuple[tuple[str, str], ...] = ()
    annotation_local_only: tuple[tuple[str, int], ...] = ()
```

Change `_write_tsv` to take the header:

```python
def _write_tsv(rows: list[dict[str, str]],
               headers: tuple[str, ...] = DWC_COLUMNS) -> str:
    """…existing docstring…"""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[c] for c in headers])
    return buf.getvalue()
```

In `export_occurrences`, change the signature to
`def export_occurrences(session: Session, cos: Iterable[CollectionObject], *, host: str | None = None) -> ExportResult:`,
add `written_cos: list[CollectionObject] = []` beside `rows`, append `written_cos.append(co)`
right after `rows.append(row)`, and replace the `return ExportResult(...)` block with:

```python
    plan = annotation_export.annotation_columns(
        session, written_cos,
        host=tw_svc.configured_host() if host is None else host)
    headers = DWC_COLUMNS + tuple(c.header for c in plan.columns)
    for co, row in zip(written_cos, rows):
        for c in plan.columns:
            row[c.header] = ""
        row.update(plan.cells.get(co.id, {}))

    return ExportResult(
        tsv="" if plan.blocked else _write_tsv(rows, headers),
        written=tuple(written),
        withheld=tuple(withheld),
        redacted=tuple(redacted),
        refused=tuple(refused),
        preparations_used=tuple(sorted(preparations_used)),
        row_count=len(written),
        annotation_columns=plan.columns,
        annotation_blocked=plan.blocked,
        annotation_local_only=plan.local_only,
    )
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS (existing export tests pass `cos` positionally and carry no annotations).

- [ ] **Step 6: Document**

`docs/taxonworks.md`, at the end of "### The emitted file (decided)", add:

```markdown
**Annotation columns (decided 2026-10-16, spec `superpowers/specs/2026-10-14-annotations-design.md`).**
Only `TW:Tag:<Class>:<tw_name>` and `TW:DataAttribute:<Class>:<tw_name>` for `CollectionObject`
and `CollectingEvent` (`darwin_core.rb:22`, `:32`), only for **linked, verified** terms the
batch actually uses, header = the TW name (never the local name). Tag cell `true` / empty,
data attribute value / empty (`occurrence.rb:1672`, `:1695`). Event annotations repeat on
every row of the event — safe only while no row makes TW reuse an event (no `eventID`): on
reuse TW silently drops event data attributes (`occurrence.rb:469`). A linked term whose
link is not confirmed on the configured host **blocks** the file; local-only terms are listed
as information; association annotations are never exported. A **term pre-flight** runs beside
the name pre-flight (id exists on this host with this name; the name resolves to exactly this
id under TW's own `uri`-then-`ILIKE` lookup, predicate selectors lower-cased — `darwin_core.rb:82`).
```

`CLAUDE.md` §5c, last bullet: replace
"No `TW:` columns yet; fix the homonym limitation of the OTU check before emitting
`TW:TaxonDetermination:otu_id`." with
"`TW:` columns: only `TW:Tag:` / `TW:DataAttribute:` for CollectionObject and
CollectingEvent, linked + verified terms only, behind a term pre-flight. Fix the homonym
limitation of the OTU check before emitting `TW:TaxonDetermination:otu_id`."

- [ ] **Step 7: Commit**

```bash
git add app/services/annotation_export.py app/services/dwc_export.py \
  tests/test_annotation_export.py docs/taxonworks.md CLAUDE.md
git commit -m "Export: TW:Tag / TW:DataAttribute columns for linked, verified terms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Term pre-flight

**Files:**
- Modify: `app/services/annotation_export.py` (add `TermCheck`, `evaluate_terms`,
  `check_terms`)
- Create: `tests/test_term_preflight.py`

**Interfaces:**
- Consumes: `ExportTerm` (Task 5); `tw_links.TwTerm`, `tw_links.fetch_candidates` (Task 3).
- Produces (used by Task 11):
  - `@dataclass(frozen=True) TermCheck(header: str, local_name: str, ok: bool,
    reason: str | None = None)`
  - `evaluate_terms(columns, candidates_by_kind: dict[str, list[TwTerm]]) -> list[TermCheck]`
  - `async check_terms(columns) -> list[TermCheck]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_term_preflight.py`:

```python
"""Term pre-flight (spec "Term pre-flight"): for every emitted column, the id exists on
this host with this name, and the name resolves to exactly this id under TW's own lookup
(``Keyword.find_by(uri: selector)`` then name ILIKE — occurrence.rb:1672 / :1695; the
predicate selector is lower-cased, darwin_core.rb:82)."""
import pytest

from app.services.annotation_export import ExportTerm, evaluate_terms
from app.services.tw_links import KEYWORD, PREDICATE, TwTerm


def _col(kind="tag", tw_id=1764, tw_name="Adventive"):
    return ExportTerm(term_id=1, kind=kind, tw_class="CollectionObject", tw_id=tw_id,
                      tw_name=tw_name, local_name="local")


def _check(col, keywords=(), predicates=()):
    (c,) = evaluate_terms([col], {KEYWORD: list(keywords), PREDICATE: list(predicates)})
    return c


def test_ok():
    assert _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive")]).ok


def test_deleted_on_taxonworks():
    c = _check(_col(), [TwTerm(KEYWORD, 5, "Endemic")])
    assert not c.ok and "no TaxonWorks keyword has id 1764" in c.reason


def test_renamed_on_taxonworks():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive species")])
    assert not c.ok and "renamed" in c.reason


def test_another_term_with_the_same_name_ignoring_case():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive"), TwTerm(KEYWORD, 8, "ADVENTIVE")])
    assert not c.ok and "id 8" in c.reason


def test_another_term_whose_uri_is_the_selector():
    c = _check(_col(), [TwTerm(KEYWORD, 1764, "Adventive"),
                        TwTerm(KEYWORD, 9, "Something", uri="Adventive")])
    assert not c.ok and "id 9" in c.reason


def test_predicate_uri_is_compared_against_the_lower_cased_selector():
    col = _col("data_attribute", 3, "EUNIS Habitat")
    c = _check(col, predicates=[TwTerm(PREDICATE, 3, "EUNIS Habitat"),
                                TwTerm(PREDICATE, 4, "Other", uri="eunis habitat")])
    assert not c.ok and "id 4" in c.reason
    ok = _check(col, predicates=[TwTerm(PREDICATE, 3, "EUNIS Habitat"),
                                 TwTerm(PREDICATE, 4, "Other", uri="EUNIS Habitat")])
    assert ok.ok                         # TW lower-cases the selector, so no clash


def test_a_tab_in_the_name_fails():
    c = _check(_col(tw_name="Adv\tentive"), [TwTerm(KEYWORD, 1764, "Adv\tentive")])
    assert not c.ok and "tab" in c.reason
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_term_preflight.py`
Expected: FAIL — `ImportError: cannot import name 'evaluate_terms'`.

- [ ] **Step 3: Implement**

Append to `app/services/annotation_export.py` (add `from app.services import tw_links` to
the imports):

```python
@dataclass(frozen=True)
class TermCheck:
    header: str
    local_name: str
    ok: bool
    reason: str | None = None


def evaluate_terms(columns, candidates_by_kind: dict) -> list[TermCheck]:
    """Pure: the four pre-flight rules against one fresh fetch per kind. Any failure
    blocks the Download — a refused export is the safe side of a CREATE-ONLY importer."""
    out: list[TermCheck] = []
    for c in columns:
        kind = c.link_kind
        cands = candidates_by_kind.get(kind, [])
        mine = next((t for t in cands if t.id == c.tw_id), None)
        reason = None
        if any(ch in c.tw_name for ch in ("\t", "\n", "\r")):
            reason = "its TaxonWorks name contains a tab or line break"
        elif mine is None:
            reason = (f"no TaxonWorks {kind} has id {c.tw_id} on this instance — run "
                      f"Verify again")
        elif mine.name != c.tw_name:
            reason = f"renamed on TaxonWorks to '{mine.name}' — run Verify again"
        else:
            # TW resolves the header's selector by `uri` first, then by name ILIKE, in the
            # project (occurrence.rb:1672 tags / :1695 data attributes); a predicate
            # selector is lower-cased before the lookup (darwin_core.rb:82).
            uri_selector = c.tw_name.lower() if kind == "predicate" else c.tw_name
            for t in cands:
                if t.id == c.tw_id:
                    continue
                if t.uri and t.uri == uri_selector:
                    reason = (f"TaxonWorks would read the column as {kind} '{t.name}' "
                              f"(id {t.id}), whose URI equals the name")
                    break
                if t.name.casefold() == c.tw_name.casefold():
                    reason = (f"TaxonWorks would read the column as {kind} '{t.name}' "
                              f"(id {t.id}) — the same name ignoring case")
                    break
        out.append(TermCheck(c.header, c.local_name, reason is None, reason))
    return out


async def check_terms(columns) -> list[TermCheck]:
    """One fresh fetch per kind the columns use, then ``evaluate_terms``. Raises
    ``TaxonWorksUnreachable`` naming the cause."""
    kinds = sorted({c.link_kind for c in columns})
    candidates = {k: await tw_links.fetch_candidates(k) for k in kinds}
    return evaluate_terms(columns, candidates)
```

- [ ] **Step 4: Run the tests**

Run: `conda run -n collection python -m pytest -q tests/test_term_preflight.py tests/test_annotation_export.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/annotation_export.py tests/test_term_preflight.py
git commit -m "Export: term pre-flight (id, name, TW's own uri/ILIKE resolution)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Compare — annotations on TaxonWorks vs local

**Files:**
- Create: `app/services/tw_annotation_compare.py`
- Create: `tests/test_tw_annotation_compare.py`
- Modify: `docs/taxonworks.md` (§5c: one paragraph after "Verified API facts")

**Interfaces:**
- Consumes: `annotations.annotations_for`, `annotations.list_terms` (Task 2);
  `taxonworks.fetch_all_pages`, `configured_host` (Task 3);
  `tw_media_compare.specimens_on_tw(session, *, repository_id, index)`;
  `tw_compare.CatalogIndex`.
- Produces (used by Task 11):
  - `@dataclass(frozen=True) AnnotationFinding(catalog_number: str, tw_object_id: int,
    target: str, kind: str, category: str, name: str, local_value: str | None = None,
    tw_value: str | None = None)` — `category` ∈ `missing_on_tw`, `only_on_tw`,
    `value_differs`, `unlinked_on_tw`
  - `@dataclass(frozen=True) AnnotationCompareResult(checked_count: int,
    findings: tuple[AnnotationFinding, ...], unverified_terms: tuple[str, ...],
    ambiguous_catalog_numbers: tuple[str, ...] = (), associations_not_compared: bool = True)`
  - `@dataclass(frozen=True) TwTag(keyword_id: int, name: str)`,
    `TwDatum(predicate_id: int | None, name: str, value: str)`
  - `async fetch_tags(object_type, ids) -> dict[int, list[TwTag]]`
  - `async fetch_data_attributes(object_type, ids) -> dict[int, list[TwDatum]]`
  - `async fetch_event_ids(co_ids) -> dict[int, int | None]`
  - `compare_annotations(session, specimens, *, host, co_tags, co_data, event_of, ev_tags,
    ev_data, ambiguous=()) -> AnnotationCompareResult`
  - `async run_annotation_compare(session, *, repository_id, index, host=None)
    -> AnnotationCompareResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tw_annotation_compare.py`:

```python
"""Annotation compare (spec "Compare"): joins on tw_id only, verified terms only, every
TW response re-verified, event annotations compared per specimen."""
import asyncio

import httpx
import pytest

import app.config as config_mod
import app.services.tw_annotation_compare as cmp
from app.config import AppConfig
from app.services import annotations as ann
from app.services.annotations import AnnotationItem
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_annotation_compare import TwDatum, TwTag
from tests.annotation_helpers import SANDBOX, SFG, make_event, make_specimen, make_term


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "_instance",
                        AppConfig(tw_base="https://sfg.taxonworks.org/api/v1", tw_token="t"))


def _serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cmp, "_client",
                        lambda: httpx.AsyncClient(transport=transport, timeout=5))


def _run(session, specimens, **kw):
    base = dict(host=SFG, co_tags={}, co_data={}, event_of={}, ev_tags={}, ev_data={})
    base.update(kw)
    return cmp.compare_annotations(session, specimens, **base)


def _cats(result):
    return sorted((f.target, f.category, f.name) for f in result.findings)


def test_each_finding_category_for_a_specimen(session):
    tag = make_term(session, name="Adventive", tw_id=1764, tw_name="Adventive", tw_host=SFG)
    tag_tw_only = make_term(session, name="Endemic", tw_id=5, tw_name="Endemic", tw_host=SFG)
    da = make_term(session, "data_attribute", "Altitude", tw_id=3, tw_name="Altitude",
                   tw_host=SFG)
    co = make_specimen(session, "ANNX-00200")
    ann.set_annotations(session, "specimen", co.id,
                        [AnnotationItem(tag.id), AnnotationItem(da.id, "350 m")])
    result = _run(session, [(co.catalog_number, co.id, 501)],
                  co_tags={501: [TwTag(5, "Endemic"), TwTag(77, "Unknown on our side")]},
                  co_data={501: [TwDatum(3, "Altitude", "400 m"),
                                 TwDatum(None, "habitat (import attribute)", "x")]},
                  event_of={501: None})
    assert _cats(result) == [
        ("specimen", "missing_on_tw", "Adventive"),
        ("specimen", "only_on_tw", "Endemic"),
        ("specimen", "unlinked_on_tw", "Unknown on our side"),
        ("specimen", "unlinked_on_tw", "habitat (import attribute)"),
        ("specimen", "value_differs", "Altitude"),
    ]
    differs = next(f for f in result.findings if f.category == "value_differs")
    assert (differs.local_value, differs.tw_value) == ("350 m", "400 m")
    assert result.associations_not_compared is True
    _ = tag_tw_only


def test_unverified_term_is_listed_not_joined(session):
    """Review Focus 4: verified on sfg, compared against the sandbox."""
    t = make_term(session, tw_id=1764, tw_name="Adventive", tw_host=SFG)
    co = make_specimen(session, "ANNX-00201")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    result = _run(session, [(co.catalog_number, co.id, 501)], host=SANDBOX,
                  co_tags={501: [TwTag(1764, "Something else on the sandbox")]},
                  event_of={501: None})
    assert result.unverified_terms == ("Adventive",)
    assert _cats(result) == [("specimen", "unlinked_on_tw", "Something else on the sandbox")]


def test_local_only_terms_are_never_compared(session):
    t = make_term(session, name="Needs re-mount")
    co = make_specimen(session, "ANNX-00202")
    ann.set_annotations(session, "specimen", co.id, [AnnotationItem(t.id)])
    assert _run(session, [(co.catalog_number, co.id, 501)], event_of={501: None}).findings == ()


def test_event_annotations_compare_per_specimen(session):
    t = make_term(session, "data_attribute", "EUNIS habitat", event=True, tw_id=3,
                  tw_name="EUNIS habitat", tw_host=SFG)
    ev = make_event(session)
    ann.set_annotations(session, "event", ev.id, [AnnotationItem(t.id, "G1.6")])
    a = make_specimen(session, "ANNX-00203", event_id=ev.id)
    b = make_specimen(session, "ANNX-00204", event_id=ev.id)
    result = _run(session, [(a.catalog_number, a.id, 501), (b.catalog_number, b.id, 502)],
                  event_of={501: 9001, 502: None},
                  ev_data={9001: [TwDatum(3, "EUNIS habitat", "G1.6")]})
    # a's TW event agrees; b's TW specimen has no event, so its event annotation is missing
    assert [(f.catalog_number, f.target, f.category) for f in result.findings] == [
        ("ANNX-00204", "event", "missing_on_tw")]


# ── fetch re-verification ──────────────────────────────────────────────────────

def test_fetch_tags_rejects_a_row_for_another_object(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 1, "tag_object_type": "CollectionObject", "tag_object_id": 999,
         "keyword_id": 5, "keyword": {"name": "Endemic"}}]))
    with pytest.raises(TaxonWorksUnreachable, match="filter did not apply"):
        asyncio.run(cmp.fetch_tags("CollectionObject", [501]))


def test_fetch_data_attributes_deduplicates_repeated_rows(cfg, monkeypatch):
    """/data_attributes has no ORDER BY (data_attributes_controller.rb:50): a row may
    repeat across pages. Distinct ids that reach the total are a complete read."""
    def row(i):
        return {"id": i, "attribute_subject_type": "CollectionObject",
                "attribute_subject_id": 501, "controlled_vocabulary_term_id": 3,
                "predicate_name": "Altitude", "value": f"{i}", "type": "InternalAttribute"}
    pages = {1: [row(1), row(2)], 2: [row(2), row(3)]}
    monkeypatch.setattr(cmp, "_PER_PAGE", 2)
    _serve(monkeypatch, lambda r: httpx.Response(
        200, json=pages[int(r.url.params["page"])], headers={"pagination-total": "3"}))
    got = asyncio.run(cmp.fetch_data_attributes("CollectionObject", [501]))
    assert sorted(d.value for d in got[501]) == ["1", "2", "3"]


def test_fetch_event_ids_refuses_a_missing_object(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, json=[
        {"id": 501, "collecting_event_id": 9001}]))
    with pytest.raises(TaxonWorksUnreachable, match="502"):
        asyncio.run(cmp.fetch_event_ids([501, 502]))


def test_empty_input_never_touches_the_network(cfg, monkeypatch):
    _serve(monkeypatch, lambda r: pytest.fail("no request expected"))
    assert asyncio.run(cmp.fetch_tags("CollectionObject", [])) == {}
    assert asyncio.run(cmp.fetch_event_ids([])) == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_tw_annotation_compare.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tw_annotation_compare'`.

- [ ] **Step 3: Implement**

Create `app/services/tw_annotation_compare.py`:

```python
"""Annotation compare (spec 2026-10-14 "Compare") — read-only, diagnostic.

Annotation divergence cannot be repaired by exporting again (the importer is
CREATE-ONLY), so Compare is the only place it surfaces. Built like
``tw_media_compare.py``: fetch functions (I/O) + a pure ``compare_annotations`` +
``run_annotation_compare``. Reuses the catalog ``index`` and
``tw_media_compare.specimens_on_tw`` (ambiguous catalog numbers excluded, never guessed).

Verified against SpeciesFileGroup/taxonworks @ 897f385:
* ``GET /api/v1/tags`` — filters ``tag_object_type``, ``tag_object_id[]``
  (``config/routes/api_v1.rb:179``; ``lib/queries/tag/filter.rb``); rows carry
  ``tag_object_type, tag_object_id, keyword_id, keyword.name``
  (``app/views/tags/api/v1/_attributes.json.jbuilder``).
* ``GET /api/v1/data_attributes`` — filters ``attribute_subject_type``,
  ``attribute_subject_id[]`` (``config/routes/api_v1.rb:151``;
  ``lib/queries/data_attribute/filter.rb``); rows carry ``type,
  controlled_vocabulary_term_id, predicate_name, import_predicate, value``
  (``app/views/data_attributes/api/v1/_attributes.json.jbuilder``). **No ORDER BY**
  (``data_attributes_controller.rb:50``) — ``fetch_all_pages`` de-duplicates. An
  ``ImportAttribute`` has no predicate id: reported as unlinked, by ``import_predicate``.
* ``GET /api/v1/collection_objects?collection_object_id[]=`` — the base filter's
  ``model_id_facet`` (``lib/queries/query/filter.rb:679``); rows carry
  ``collecting_event_id`` (``app/views/collection_objects/api/v1/_attributes.json.jbuilder:1``).
  Each imported row created its own TW event, so event annotations are compared PER
  SPECIMEN.

Only terms whose link is verified on the configured host are compared — a ``tw_id`` from
another instance names something else. Joins are on ``tw_id``, never on names.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

import app.services.taxonworks as tw
from app.models import CollectionObject
from app.services import annotations as ann
from app.services import tw_media_compare
from app.services.taxonworks import TaxonWorksUnreachable
from app.services.tw_compare import CatalogIndex

_TIMEOUT = httpx.Timeout(25.0)
_BATCH = 100        # ids per request — keeps the GET URL short
_PER_PAGE = 500


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT)


@dataclass(frozen=True)
class AnnotationFinding:
    catalog_number: str
    tw_object_id: int
    target: str                    # specimen | event
    kind: str                      # tag | data_attribute
    category: str                  # missing_on_tw | only_on_tw | value_differs | unlinked_on_tw
    name: str
    local_value: str | None = None
    tw_value: str | None = None


@dataclass(frozen=True)
class AnnotationCompareResult:
    checked_count: int
    findings: tuple[AnnotationFinding, ...]
    unverified_terms: tuple[str, ...]        # linked, not verified here: not compared
    ambiguous_catalog_numbers: tuple[str, ...] = ()
    associations_not_compared: bool = True   # stated in the report, like media_not_compared


@dataclass(frozen=True)
class TwTag:
    keyword_id: int
    name: str


@dataclass(frozen=True)
class TwDatum:
    predicate_id: int | None
    name: str
    value: str


def _batches(ids: list[int]):
    for i in range(0, len(ids), _BATCH):
        yield ids[i:i + _BATCH]


def _foreign(what: str, row_type, row_id) -> TaxonWorksUnreachable:
    return TaxonWorksUnreachable(
        f"{what} returned a row for {row_type} #{row_id} — the filter did not apply. "
        f"Treated as a lookup failure rather than as a set of matches.")


async def fetch_tags(object_type: str, ids: list[int]) -> dict[int, list[TwTag]]:
    out: dict[int, list[TwTag]] = defaultdict(list)
    if not ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(ids))):
            wanted = set(batch)
            what = f"the TaxonWorks tags of {len(batch)} {object_type} record(s)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if (row.get("tag_object_type") != object_type
                        or row.get("tag_object_id") not in wanted):
                    raise _foreign(what, row.get("tag_object_type"), row.get("tag_object_id"))
                if not isinstance(row.get("keyword_id"), int):
                    raise TaxonWorksUnreachable(f"{what} returned a tag without a keyword id.")

            rows = await tw.fetch_all_pages(
                client, "/tags",
                {"tag_object_type": object_type, "tag_object_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                name = str((r.get("keyword") or {}).get("name") or "").strip()
                out[r["tag_object_id"]].append(TwTag(r["keyword_id"], name))
    return dict(out)


async def fetch_data_attributes(object_type: str, ids: list[int]) -> dict[int, list[TwDatum]]:
    out: dict[int, list[TwDatum]] = defaultdict(list)
    if not ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(ids))):
            wanted = set(batch)
            what = f"the TaxonWorks data attributes of {len(batch)} {object_type} record(s)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if (row.get("attribute_subject_type") != object_type
                        or row.get("attribute_subject_id") not in wanted):
                    raise _foreign(what, row.get("attribute_subject_type"),
                                   row.get("attribute_subject_id"))

            rows = await tw.fetch_all_pages(
                client, "/data_attributes",
                {"attribute_subject_type": object_type, "attribute_subject_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                pid = r.get("controlled_vocabulary_term_id")
                if isinstance(pid, int):
                    name = str(r.get("predicate_name") or "").strip()
                else:
                    pid = None
                    name = f"{str(r.get('import_predicate') or '').strip()} (import attribute)"
                out[r["attribute_subject_id"]].append(
                    TwDatum(pid, name, str(r.get("value") or "").strip()))
    return dict(out)


async def fetch_event_ids(co_ids: list[int]) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    if not co_ids:
        return {}
    async with _client() as client:
        for batch in _batches(sorted(set(co_ids))):
            wanted = set(batch)
            what = f"the TaxonWorks collection objects ({len(batch)} requested)"

            def check(row: dict, wanted=wanted, what=what) -> None:
                if row.get("id") not in wanted:
                    raise _foreign(what, "CollectionObject", row.get("id"))

            rows = await tw.fetch_all_pages(
                client, "/collection_objects", {"collection_object_id[]": batch},
                what=what, check_row=check, per_page=_PER_PAGE)
            for r in rows:
                ev = r.get("collecting_event_id")
                out[r["id"]] = ev if isinstance(ev, int) else None
            missing = sorted(wanted - {r["id"] for r in rows})
            if missing:
                raise TaxonWorksUnreachable(
                    f"{what} did not return CollectionObject(s) {missing} that the "
                    f"identifier index lists — treated as a lookup failure.")
    return out


def _diff(findings: list, cat: str, tw_object_id: int, target: str, local: list,
          tw_tags: list[TwTag], tw_data: list[TwDatum], verified: dict) -> None:
    tag_by_twid = {t.tw_id: t for t in verified.values() if t.kind == "tag"}
    da_by_twid = {t.tw_id: t for t in verified.values() if t.kind == "data_attribute"}

    def add(kind, category, name, local_value=None, tw_value=None):
        findings.append(AnnotationFinding(cat, tw_object_id, target, kind, category, name,
                                          local_value, tw_value))

    local_tags = {a.term.tw_id: a.term for a in local
                  if a.term_kind == "tag" and a.term_id in verified}
    local_data = {a.term.tw_id: (a.term, a.value) for a in local
                  if a.term_kind == "data_attribute" and a.term_id in verified}

    tw_tag_ids = {t.keyword_id: t for t in tw_tags}
    for twid, term in local_tags.items():
        if twid not in tw_tag_ids:
            add("tag", "missing_on_tw", term.name)
    for twid, t in tw_tag_ids.items():
        if twid in local_tags:
            continue
        if twid in tag_by_twid:
            add("tag", "only_on_tw", tag_by_twid[twid].name)
        else:
            add("tag", "unlinked_on_tw", t.name)

    tw_values: dict[int, list[str]] = defaultdict(list)
    tw_names: dict[int, str] = {}
    for d in tw_data:
        if d.predicate_id is None:
            add("data_attribute", "unlinked_on_tw", d.name, tw_value=d.value)
            continue
        tw_values[d.predicate_id].append(d.value)
        tw_names.setdefault(d.predicate_id, d.name)
    for twid, (term, value) in local_data.items():
        values = tw_values.get(twid)
        if not values:
            add("data_attribute", "missing_on_tw", term.name, local_value=value)
        elif value not in values:
            add("data_attribute", "value_differs", term.name, local_value=value,
                tw_value=" | ".join(sorted(values)))
    for twid, values in tw_values.items():
        if twid in local_data:
            continue
        joined = " | ".join(sorted(values))
        if twid in da_by_twid:
            add("data_attribute", "only_on_tw", da_by_twid[twid].name, tw_value=joined)
        else:
            add("data_attribute", "unlinked_on_tw", tw_names[twid], tw_value=joined)


def compare_annotations(session: Session, specimens: list[tuple[str, int, int]], *,
                        host: str, co_tags: dict, co_data: dict, event_of: dict,
                        ev_tags: dict, ev_data: dict,
                        ambiguous: tuple[str, ...] = ()) -> AnnotationCompareResult:
    """Pure computation, no I/O. ``specimens`` is ``(catalog_number, local co id, TW co
    id)`` from ``tw_media_compare.specimens_on_tw``."""
    terms = ann.list_terms(session)
    verified = {t.id: t for t in terms if t.tw_id is not None and t.tw_host == host}
    unverified = tuple(sorted({t.name for t in terms
                               if t.tw_id is not None and t.tw_host != host}))
    findings: list[AnnotationFinding] = []
    for cat, co_id, tw_id in specimens:
        co = session.get(CollectionObject, co_id)
        _diff(findings, cat, tw_id, "specimen", ann.annotations_for(session, "specimen", co_id),
              co_tags.get(tw_id, []), co_data.get(tw_id, []), verified)
        local_event = (ann.annotations_for(session, "event", co.collecting_event_id)
                       if co.collecting_event_id else [])
        tw_event = event_of.get(tw_id)
        _diff(findings, cat, tw_id, "event", local_event,
              ev_tags.get(tw_event, []) if tw_event else [],
              ev_data.get(tw_event, []) if tw_event else [], verified)
    return AnnotationCompareResult(
        checked_count=len(specimens), findings=tuple(findings), unverified_terms=unverified,
        ambiguous_catalog_numbers=tuple(ambiguous))


async def run_annotation_compare(session: Session, *, repository_id: int, index: CatalogIndex,
                                 host: str | None = None) -> AnnotationCompareResult:
    """I/O + computation — the entry point the TaxonWorks tab calls."""
    host = tw.configured_host() if host is None else host
    specimens, ambiguous = tw_media_compare.specimens_on_tw(
        session, repository_id=repository_id, index=index)
    tw_ids = [tw_id for _cat, _co, tw_id in specimens]
    co_tags = await fetch_tags("CollectionObject", tw_ids)
    co_data = await fetch_data_attributes("CollectionObject", tw_ids)
    event_of = await fetch_event_ids(tw_ids)
    ev_ids = sorted({e for e in event_of.values() if e})
    ev_tags = await fetch_tags("CollectingEvent", ev_ids)
    ev_data = await fetch_data_attributes("CollectingEvent", ev_ids)
    return compare_annotations(session, specimens, host=host, co_tags=co_tags,
                               co_data=co_data, event_of=event_of, ev_tags=ev_tags,
                               ev_data=ev_data, ambiguous=ambiguous)
```

- [ ] **Step 4: Run the tests**

Run: `conda run -n collection python -m pytest -q tests/test_tw_annotation_compare.py`
Expected: PASS. If `_cats` ordering differs, the assertion is on a sorted list — fix the
implementation, not the test.

- [ ] **Step 5: Document**

`docs/taxonworks.md` §5c, at the end of "### Verified API facts", add:

```markdown
**Annotation compare (verified @ `897f385`, 2026-10-16).** Tags and data attributes are read
**by object**: `/tags?tag_object_type=…&tag_object_id[]=…` (`api_v1.rb:179`) and
`/data_attributes?attribute_subject_type=…&attribute_subject_id[]=…` (`api_v1.rb:151`),
every row re-checked against the requested type and ids. `/data_attributes` (and
`/controlled_vocabulary_terms`) page **without an ORDER BY** (`data_attributes_controller.rb:50`),
so rows are de-duplicated by id and fewer distinct ids than `pagination-total` is a lookup
failure (`taxonworks.fetch_all_pages`). A TW `ImportAttribute` has no predicate id and is
reported as an unlinked term under its `import_predicate`. A specimen's TW event comes from
`/collection_objects?collection_object_id[]=…` (base filter `model_id_facet`,
`lib/queries/query/filter.rb:679`; field `collecting_event_id`); each imported row created
its own TW event, so event annotations are compared **per specimen**. Only terms whose link
is verified on the configured host are compared; joins are on `tw_id`, never on names.
```

- [ ] **Step 6: Commit**

```bash
git add app/services/tw_annotation_compare.py tests/test_tw_annotation_compare.py docs/taxonworks.md
git commit -m "Compare: annotations on TaxonWorks vs local (specimens + their events)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Controlled Vocabularies — "Tags & data attributes" and "Biological relationships"

**Files:**
- Create: `app/ui/merge_target_list.py` (moved verbatim from `controlled_vocab_tab.py`)
- Create: `app/ui/annotation_chips.py`
- Create: `app/ui/tw_vocab_section.py`
- Modify: `app/ui/controlled_vocab_tab.py` (import the moved picker; register two sections)
- Create: `tests/test_annotation_chips.py`

**Interfaces:**
- Consumes: Tasks 2–4 (`annotations.*`, `tw_links.*`, `biological.relationship_vocab`,
  `create_relationship`, `rename_relationship`, `merge_relationships`,
  `taxonworks.configured_host`).
- Produces (used by Task 9):
  - `annotation_chips.CSS: str`, `text_color(css_color: str) -> str`,
    `chip_html(name, css_color=None, *, local_only=False) -> str`,
    `swatch_html(css_color: str | None) -> str`,
    `annotations_html(views: list[AnnotationView]) -> str`
  - `merge_target_list.build_merge_target_list(on_pick) -> {"show": callable}`
  - `tw_vocab_section.build_annotation_terms_section(session_factory) -> None`,
    `build_relationships_section(session_factory) -> None`

- [ ] **Step 1: Write the failing chip tests**

Create `tests/test_annotation_chips.py`:

```python
"""Chip rendering (spec "Colour"): black or white text by the colour's luminance; names
are escaped; no colour = the neutral chip."""
from app.services.annotations import AnnotationView
from app.ui.annotation_chips import annotations_html, chip_html, swatch_html, text_color


def test_text_color_by_luminance():
    assert text_color("#254d0d") == "#fff"      # dark green (Curculionoidea associations)
    assert text_color("#ff8800") == "#000"      # orange (asserted distribution)
    assert text_color("#fff") == "#000"
    assert text_color("#000") == "#fff"


def test_chip_escapes_and_colours():
    html = chip_html("<b>Adventive</b>", "#254d0d", local_only=True)
    assert "&lt;b&gt;" in html and "background:#254d0d" in html and "color:#fff" in html
    assert "local" in html
    assert "background" not in chip_html("Adventive")


def test_swatch_without_colour_is_a_placeholder():
    assert "dashed" in swatch_html(None)
    assert "#abc" in swatch_html("#abc")


def test_annotations_html_tags_then_data_attributes():
    views = [AnnotationView(1, "tag", "Adventive", None, "#ff8800", True),
             AnnotationView(2, "data_attribute", "EUNIS habitat", "G1.6", None, False)]
    html = annotations_html(views)
    assert html.index("Adventive") < html.index("EUNIS habitat")
    assert "G1.6" in html
    assert annotations_html([]) == ""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_chips.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ui.annotation_chips'`.

- [ ] **Step 3: Write `annotation_chips.py`**

```python
"""Annotation chip rendering — pure HTML helpers shared by the record sheets, the
annotation popup and the vocabulary table (spec "Entering annotations" → Colour).

A term's ``css_color`` reaches here only as ``#rgb`` / ``#rrggbb`` — the DB CHECK
``ck_annotation_term_css_color`` guarantees it — so it is safe to inline in a style
attribute. Names and values are HTML-escaped. Inspired by the Curculionoidea TaxonWorks
contributor docs, which colour keyword chips from TW's ``css_color``.
"""
from __future__ import annotations

import html as _html

CSS = """<style>
.ann-chip  { display:inline-flex; align-items:center; gap:4px; font-size:.75rem;
             line-height:1.25; padding:1px 8px; border-radius:999px;
             border:1px solid var(--tp-base-border,#e2e8f0);
             background:rgba(148,163,184,.15); color:inherit; white-space:nowrap; }
.ann-local { font-size:.58rem; text-transform:uppercase; letter-spacing:.04em; opacity:.7; }
.ann-da    { display:inline-flex; align-items:center; gap:4px; font-size:.82rem; }
.ann-line  { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.ann-swatch { display:inline-block; width:14px; height:14px; border-radius:3px;
              vertical-align:middle; }
</style>"""


def text_color(css_color: str) -> str:
    """``#000`` or ``#fff``, whichever reads on this background (WCAG relative luminance,
    threshold 0.179 — the point where black and white give equal contrast)."""
    h = css_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return "#000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.179 else "#fff"


def chip_html(name: str, css_color: str | None = None, *, local_only: bool = False) -> str:
    style = (f' style="background:{css_color};border-color:{css_color};'
             f'color:{text_color(css_color)}"' if css_color else "")
    badge = '<span class="ann-local">local</span>' if local_only else ""
    return f'<span class="ann-chip"{style}>{_html.escape(name)}{badge}</span>'


def swatch_html(css_color: str | None) -> str:
    if not css_color:
        return ('<span class="ann-swatch" style="border:1px dashed '
                'var(--tp-base-soft,#9ca3af)"></span>')
    return (f'<span class="ann-swatch" style="background:{css_color};'
            f'border:1px solid rgba(0,0,0,.2)"></span>')


def annotations_html(views) -> str:
    """Tags as chips, then data attributes as ``chip: value``; "" when there are none."""
    if not views:
        return ""
    parts = [chip_html(v.name, v.css_color, local_only=not v.linked)
             for v in views if v.kind == "tag"]
    parts += [f'<span class="ann-da">{chip_html(v.name, v.css_color, local_only=not v.linked)}'
              f'{_html.escape(v.value or "")}</span>'
              for v in views if v.kind == "data_attribute"]
    return f'<div class="ann-line">{"".join(parts)}</div>'
```

- [ ] **Step 4: Move the merge picker**

Create `app/ui/merge_target_list.py` containing exactly `_MERGE_LIST_CAP` and the body of
`_build_merge_target_list` from `app/ui/controlled_vocab_tab.py` (lines 435–498 today),
renamed `build_merge_target_list`, with this header:

```python
"""The "entry to keep" picker of a merge dialog — search box + scrolling radio list.

Defined once for every merge dialog (persons, each vocabulary, annotation terms,
biological relationships): any of these lists can run to hundreds of rows, so all of them
filter by typing, scroll inside the dialog and render at most ``_MERGE_LIST_CAP`` rows.
"""
from __future__ import annotations

from nicegui import ui
```

In `controlled_vocab_tab.py`: delete `_MERGE_LIST_CAP` and `_build_merge_target_list`, add
`from app.ui.merge_target_list import build_merge_target_list`, and replace both calls
`_build_merge_target_list(` with `build_merge_target_list(`.

- [ ] **Step 5: Write `tw_vocab_section.py`**

Create `app/ui/tw_vocab_section.py`:

```python
"""Controlled Vocabularies → "Tags & data attributes" and "Biological relationships"
(spec 2026-10-14 "Controlled Vocabularies tab — two new sub-tabs").

Both sub-tabs are one builder: a table with a link chip per row, a Verify button (one
fetch per kind, user-triggered only), a link dialog (searchable TaxonWorks list; a TW term
already linked to another local entry is shown disabled with that entry's name), and the
usual edit / merge / delete / add. Nothing is decided silently: a renamed TW term opens a
dialog with use TW name / keep local name / unlink per entry.
"""
from __future__ import annotations

from nicegui import ui

import app.services.annotations as ann
import app.services.biological as bio
import app.services.taxonworks as tw_svc
import app.services.tw_links as links
from app.models import AnnotationTerm, BiologicalRelationship
from app.ui.annotation_chips import CSS as CHIP_CSS, chip_html, swatch_html
from app.ui.merge_target_list import build_merge_target_list

_KIND_LABEL = {"tag": "Tag", "data_attribute": "Data attribute"}
_LINK_KIND = {"tag": links.KEYWORD, "data_attribute": links.PREDICATE}
_TERM_KIND = {links.KEYWORD: "tag", links.PREDICATE: "data_attribute"}
_STATE_CHIP = {"local_only": ("local only", "grey-6"),
               "verified": ("TaxonWorks ✓", "positive"),
               "not_verified": ("not verified", "warning")}
_ALLOW_COLS = (("allow_specimen", "specimens"), ("allow_event", "events"),
               ("allow_association", "associations"))


def build_annotation_terms_section(session_factory) -> None:
    _build_section(session_factory, terms=True)


def build_relationships_section(session_factory) -> None:
    _build_section(session_factory, terms=False)


def _chip(row, host: str, detail: dict) -> tuple[str, str]:
    state = links.link_state(row, host)
    outcome = detail.get(row.id)
    if state == "not_verified" and outcome is not None:
        if outcome.state == "name_differs":
            return f'name differs: "{outcome.tw_name}"', "negative"
        if outcome.state == "not_found":
            return "not found on this instance", "negative"
    return _STATE_CHIP[state]


def _build_section(session_factory, *, terms: bool) -> None:
    ui.add_head_html(CHIP_CSS)
    model = AnnotationTerm if terms else BiologicalRelationship
    kinds = (links.KEYWORD, links.PREDICATE) if terms else (links.RELATIONSHIP,)
    detail: dict[int, links.VerifyOutcome] = {}   # latest Verify, this session only

    with ui.card().classes("w-full shadow-sm"):
        with ui.row().classes("items-center gap-2 mb-1 w-full"):
            ui.label("Tags & data attributes" if terms else "Biological relationships") \
                .classes("section-label")
            ui.label(
                "Flags and name → value facts for specimens, events and associations. "
                "Link a term to TaxonWorks to export it; an unlinked term stays local."
                if terms else
                "The relationship vocabulary of biological associations (e.g. feeds on). "
                "An optional TaxonWorks link is set here only — never on launch."
            ).classes("text-sm").style("color:var(--tp-base-soft)")
            ui.space()
            verify_btn = ui.button("Verify", icon="verified") \
                .props("flat no-caps color=secondary") \
                .tooltip("Fetch the TaxonWorks list once and re-check every link")
        ui.separator().classes("mb-3")

        def _rows() -> list[dict]:
            host = tw_svc.configured_host()
            out = []
            with session_factory() as s:
                for kind in kinds:
                    for r in links.rows_of_kind(s, kind):
                        label, color = _chip(r, host, detail)
                        row = {"id": r.id, "name": r.name, "chip": label,
                               "chip_color": color, "linked": r.tw_id is not None,
                               "link_kind": kind}
                        if terms:
                            row.update(kind=_KIND_LABEL[r.kind], css_color=r.css_color or "",
                                       **{c: bool(getattr(r, c)) for c, _ in _ALLOW_COLS})
                        out.append(row)
            return out

        cols = [{"name": "name", "label": "Name", "field": "name", "align": "left",
                 "sortable": True}]
        if terms:
            cols += [{"name": "kind", "label": "Type", "field": "kind", "align": "left",
                      "sortable": True},
                     {"name": "allow", "label": "Offered for", "field": "id", "align": "left"},
                     {"name": "colour", "label": "Colour", "field": "css_color",
                      "align": "left"}]
        cols += [{"name": "chip", "label": "TaxonWorks", "field": "chip", "align": "left"},
                 {"name": "actions", "label": "", "field": "id", "align": "right"}]
        table = ui.table(columns=cols, rows=_rows(), row_key="id") \
            .classes("w-full").props("flat dense")
        if terms:
            toggles = "".join(
                f'<q-toggle dense size="xs" label="{label}" '
                f':model-value="props.row.{col}" '
                f"@update:model-value=\"v => $parent.$emit('allow', "
                f"{{id: props.row.id, field: '{col}', value: v}})\" />"
                for col, label in _ALLOW_COLS)
            table.add_slot("body-cell-allow", f'<q-td :props="props">{toggles}</q-td>')
            table.add_slot("body-cell-colour", """
                <q-td :props="props">
                  <span v-if="props.row.css_color" class="ann-swatch"
                        :style="{background: props.row.css_color,
                                 border: '1px solid rgba(0,0,0,.2)'}"></span>
                </q-td>""")
        table.add_slot("body-cell-chip", """
            <q-td :props="props">
              <q-badge :color="props.row.chip_color" :label="props.row.chip"
                       class="cursor-pointer" @click="$parent.$emit('link', props.row)" />
            </q-td>""")
        table.add_slot("body-cell-actions", """
            <q-td :props="props">
              <q-btn v-if="!props.row.linked" flat dense round icon="link" size="xs"
                     title="Link to TaxonWorks" @click="$parent.$emit('link', props.row)" />
              <q-btn v-else flat dense round icon="link_off" size="xs"
                     title="Unlink — the entry becomes local only"
                     @click="$parent.$emit('unlink', props.row)" />
              <q-btn flat dense round icon="edit" size="xs"
                     @click="$parent.$emit('edit', props.row)" />
              <q-btn flat dense round icon="merge_type" size="sm" style="color:#f97316"
                     title="Merge with another entry"
                     @click="$parent.$emit('merge', props.row)" />
              <q-btn flat dense round icon="delete" size="xs" color="negative"
                     @click="$parent.$emit('delete', props.row)" />
            </q-td>""")

        def _refresh() -> None:
            table.rows = _rows()
            table.update()

        ui.timer(2.0, _refresh)

        # ── writes ──────────────────────────────────────────────────────────
        def _write(fn, ok_msg: str | None = None) -> bool:
            try:
                with session_factory() as s:
                    with s.begin():
                        fn(s)
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True, timeout=8000)
                return False
            except Exception as exc:
                ui.notify(f"Failed: {exc}", type="negative", multi_line=True)
                return False
            _refresh()
            if ok_msg:
                ui.notify(ok_msg, type="positive")
            return True

        def _allow(args: dict) -> None:
            _write(lambda s: ann.update_term(s, int(args["id"]),
                                             **{args["field"]: bool(args["value"])}))

        def _unlink(row: dict) -> None:
            detail.pop(int(row["id"]), None)
            _write(lambda s: links.unlink(s, s.get(model, int(row["id"]))),
                   f"'{row['name']}' is local only now.")

        def _delete(row: dict) -> None:
            if terms:
                _write(lambda s: ann.delete_term(s, int(row["id"])), "Deleted.")
            else:
                _write(lambda s: bio.relationship_vocab.delete(s, int(row["id"])), "Deleted.")

        # ── link dialog ─────────────────────────────────────────────────────
        async def _open_link(kind: str, row_id: int | None,
                             create: dict | None = None) -> None:
            """Link an existing row (``row_id``) or create one from a TW term (``create``
            holds the new term's allow flags)."""
            host = tw_svc.configured_host()
            try:
                cands = await links.fetch_candidates(kind)
            except tw_svc.TaxonWorksUnreachable as exc:
                ui.notify(str(exc), type="negative", multi_line=True, timeout=8000)
                return
            with session_factory() as s:
                held = {r.tw_id: r.name for r in links.rows_of_kind(s, kind)
                        if r.tw_id is not None and r.id != row_id}

            with ui.dialog() as dlg, ui.card().classes("w-[540px]"):
                ui.label(f"Link to a TaxonWorks {kind}").classes("section-label mb-1")
                ui.label(f"From {host}. Picking a term links it and confirms it at once.") \
                    .classes("text-xs").style("color:var(--tp-base-soft)")
                search = ui.input(placeholder="Search TaxonWorks…") \
                    .props("dense outlined clearable debounce=150").classes("w-full mt-2")
                box = ui.element("div").classes("w-full mt-1 overflow-y-auto") \
                    .style("max-height:20rem")

                def _pick(c: links.TwTerm) -> None:
                    warning: list[str | None] = [None]

                    def fn(s):
                        if row_id is None:
                            row = (ann.create_term(s, kind=_TERM_KIND[kind], name=c.name,
                                                   **(create or {}))
                                   if terms else bio.create_relationship(s, c.name))
                        else:
                            row = s.get(model, row_id)
                        warning[0] = links.link(s, row, c, host)
                    if not _write(fn, f"Linked to TaxonWorks '{c.name}'."):
                        return
                    if row_id is not None:
                        detail.pop(row_id, None)
                    if warning[0]:
                        ui.notify(warning[0], type="warning", multi_line=True, timeout=8000)
                    dlg.close()

                def _render() -> None:
                    needle = (search.value or "").strip().casefold()
                    hits = [c for c in cands if needle in c.name.casefold()]
                    box.clear()
                    with box:
                        if not hits:
                            ui.label("No TaxonWorks term matches.").classes("text-sm px-3 py-2") \
                                .style("color:var(--tp-base-soft)")
                        for c in hits[:200]:
                            holder = held.get(c.id)
                            el = ui.element("div").classes(
                                "w-full flex items-center gap-2 px-3 py-2 rounded "
                                + ("opacity-50" if holder else
                                   "cursor-pointer hover:bg-slate-50 dark:hover:bg-white/5"))
                            with el:
                                colour, _w = ann.tw_css_color(c.css_color)
                                ui.html(chip_html(c.name, colour))
                                ui.label(f"id {c.id}").classes("text-xs") \
                                    .style("color:var(--tp-base-soft)")
                                if holder:
                                    ui.label(f"linked to '{holder}'").classes("text-xs ml-auto")
                            if not holder:
                                el.on("click", lambda _, c=c: _pick(c))

                search.on_value_change(lambda _: _render())
                _render()
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        # ── verify + name-differs resolution ────────────────────────────────
        def _open_name_differs(outcomes: list[links.VerifyOutcome]) -> None:
            host = tw_svc.configured_host()
            with ui.dialog() as dlg, ui.card().classes("w-[580px]"):
                ui.label("Renamed on TaxonWorks").classes("section-label")
                ui.label("Choose per entry — nothing is decided for you.") \
                    .classes("text-sm").style("color:var(--tp-base-soft)")
                for o in outcomes:
                    with ui.column().classes("w-full gap-1 mt-2") as rbox:
                        ui.label(f"'{o.name}' — TaxonWorks now calls it '{o.tw_name}'") \
                            .classes("text-sm font-medium")
                        with ui.row().classes("gap-2"):
                            for choice, label in (("use_tw", "Use TW name"),
                                                  ("keep_local", "Keep local name"),
                                                  ("unlink", "Unlink")):
                                def _resolve(_, o=o, choice=choice, rbox=rbox):
                                    if _write(lambda s: links.resolve_name_difference(
                                            s, s.get(model, o.row_id), choice, o.tw_name, host)):
                                        detail.pop(o.row_id, None)
                                        rbox.clear()
                                        with rbox:
                                            ui.label(f"'{o.name}': done.").classes("text-sm")
                                ui.button(label, on_click=_resolve) \
                                    .props("flat dense no-caps color=secondary")
                with ui.row().classes("w-full justify-end mt-3"):
                    ui.button("Close", on_click=dlg.close).props("flat no-caps")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        async def _verify() -> None:
            verify_btn.props("loading")
            outcomes: list[links.VerifyOutcome] = []
            try:
                # The session spans the fetches on purpose: verify() applies its result
                # to the rows it reads; commit only once every kind has been checked.
                with session_factory() as s:
                    for kind in kinds:
                        outcomes += await links.verify(s, kind)
                    s.commit()
            except tw_svc.TaxonWorksUnreachable as exc:
                ui.notify(str(exc), type="negative", multi_line=True, timeout=8000)
                return
            finally:
                verify_btn.props(remove="loading")
            detail.clear()
            detail.update({o.row_id: o for o in outcomes})
            _refresh()
            for o in outcomes:
                if o.color_warning:
                    ui.notify(f"{o.name}: {o.color_warning}", type="warning", multi_line=True)
            ok = sum(o.state == "verified" for o in outcomes)
            ui.notify(f"{ok} of {len(outcomes)} link(s) confirmed on "
                      f"{tw_svc.configured_host()}.",
                      type="positive" if ok == len(outcomes) else "warning")
            differs = [o for o in outcomes if o.state == "name_differs"]
            if differs:
                _open_name_differs(differs)

        verify_btn.on_click(_verify)

        # ── edit dialog ─────────────────────────────────────────────────────
        def _open_edit(row: dict) -> None:
            rid = int(row["id"])
            with session_factory() as s:
                obj = s.get(model, rid)
                name0, linked = obj.name, obj.tw_id is not None
                colour0 = (obj.css_color or "") if terms else ""
            with ui.dialog() as dlg, ui.card().classes("w-96"):
                ui.label("Edit term" if terms else "Edit relationship") \
                    .classes("section-label mb-2")
                name_in = ui.input("Name *", value=name0).classes("w-full")
                colour_in = None
                if terms:
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        colour_in = ui.input("Colour", value=colour0,
                                             placeholder="#254d0d").classes("flex-1")
                        sw = ui.html(swatch_html(colour0 or None))

                    def _live(_):
                        try:
                            sw.set_content(swatch_html(ann.normalize_css_color(colour_in.value)))
                        except ValueError:
                            sw.set_content(swatch_html(None))
                    colour_in.on_value_change(_live)
                    if linked:
                        colour_in.props("readonly")
                        ui.label("Colour from TaxonWorks while linked.").classes("text-xs") \
                            .style("color:var(--tp-base-soft)")

                def _save() -> None:
                    if terms:
                        kw = {"name": name_in.value}
                        if not linked:
                            kw["css_color"] = colour_in.value
                        fn = lambda s: ann.update_term(s, rid, **kw)
                    else:
                        fn = lambda s: bio.rename_relationship(s, rid, name_in.value)
                    if _write(fn, "Saved."):
                        dlg.close()

                with ui.row().classes("w-full justify-end gap-2 mt-3"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                    ui.button("Save", on_click=_save).props("no-caps color=secondary")
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        # ── merge dialog ────────────────────────────────────────────────────
        def _open_merge(row: dict) -> None:
            absorb_id = int(row["id"])
            with session_factory() as s:
                absorb = s.get(model, absorb_id)
                absorb_name, absorb_linked = absorb.name, absorb.tw_id is not None
                others = {r.id: r.name for r in links.rows_of_kind(s, links.kind_of(absorb))
                          if r.id != absorb_id}
            chosen: dict = {"keep": None}
            with ui.dialog() as dlg, ui.card().classes("w-[500px]"):
                ui.label("Merge").classes("section-label mb-2")
                ui.label(f"Absorbed — deleted after merge: {absorb_name}") \
                    .classes("text-sm font-semibold").style("color:#f97316")
                keep_lbl = ui.label("— pick the entry to keep below —").classes("text-sm")
                targets = build_merge_target_list(lambda oid: _pick(oid))
                info = ui.label("").classes("text-sm mt-2").style("color:var(--tp-base-soft)")
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                    confirm = ui.button("Merge", icon="merge_type").props("no-caps") \
                        .style("background:#f97316; color:white")
                confirm.set_enabled(False)

            def _pick(oid: int) -> None:
                chosen["keep"] = oid
                targets["show"](others, oid)
                keep_lbl.set_text(f"Kept — receives all references: {others[oid]}")
                ok, msg = True, ""
                if absorb_linked:
                    ok, msg = False, f"Unlink '{absorb_name}' first — a merge never moves a link."
                else:
                    with session_factory() as s:
                        if terms:
                            try:
                                p = ann.merge_preview(s, keep_id=oid, absorb_id=absorb_id)
                            except ValueError as exc:
                                ok, msg = False, str(exc)
                            else:
                                ok = not p.conflicts
                                msg = (f"{p.repoint} annotation(s) re-pointed, {p.fold} "
                                       f"duplicate(s) folded." if ok else
                                       "Refused — different values on: "
                                       + "; ".join(p.conflicts[:5]))
                        else:
                            p = bio.relationship_vocab.merge_preview(
                                s, keep_id=oid, absorb_id=absorb_id)
                            msg = f"{p.reference_count} association(s) re-pointed."
                info.set_text(msg)
                confirm.set_enabled(ok)

            def _confirm() -> None:
                keep = chosen["keep"]
                fn = ((lambda s: ann.merge_terms(s, keep_id=keep, absorb_id=absorb_id))
                      if terms else
                      (lambda s: bio.merge_relationships(s, keep_id=keep, absorb_id=absorb_id)))
                if _write(fn, "Merged."):
                    dlg.close()

            confirm.on_click(_confirm)
            targets["show"](others, None, reset_search=True)
            dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
            dlg.open()

        table.on("link", lambda e: _open_link(e.args["link_kind"], int(e.args["id"])))
        table.on("unlink", lambda e: _unlink(e.args))
        table.on("edit", lambda e: _open_edit(e.args))
        table.on("merge", lambda e: _open_merge(e.args))
        table.on("delete", lambda e: _delete(e.args))
        if terms:
            table.on("allow", lambda e: _allow(e.args))

        # ── add ─────────────────────────────────────────────────────────────
        ui.separator().classes("my-3")
        ui.label("Add a term" if terms else "Add a relationship") \
            .classes("text-sm font-semibold mb-2")
        with ui.row().classes("w-full gap-3 items-end flex-wrap"):
            add_name = ui.input("Name *").classes("flex-1 min-w-[12rem]")
            add_kind = None
            allow_boxes: dict = {}
            if terms:
                add_kind = ui.select({"tag": "Tag", "data_attribute": "Data attribute"},
                                     value="tag", label="Type").classes("w-40")
                for col, label in _ALLOW_COLS:
                    allow_boxes[col] = ui.checkbox(label, value=(col == "allow_specimen"))

            def _flags() -> dict:
                return {c: bool(cb.value) for c, cb in allow_boxes.items()}

            def _add_local() -> None:
                if terms:
                    fn = lambda s: ann.create_term(s, kind=add_kind.value, name=add_name.value,
                                                   **_flags())
                else:
                    fn = lambda s: bio.create_relationship(s, add_name.value)
                if _write(fn, "Added."):
                    add_name.value = ""

            async def _add_from_tw() -> None:
                kind = _LINK_KIND[add_kind.value] if terms else links.RELATIONSHIP
                await _open_link(kind, None, create=_flags() if terms else None)

            ui.button("Add local", icon="add", on_click=_add_local) \
                .props("flat no-caps color=secondary")
            ui.button("Add from TaxonWorks", icon="cloud_download", on_click=_add_from_tw) \
                .props("flat no-caps color=secondary")
```

- [ ] **Step 6: Register the two sections**

In `controlled_vocab_tab.py`: add
`from app.ui.tw_vocab_section import build_annotation_terms_section, build_relationships_section`;
change `_sections` to

```python
    _sections: list[tuple[str, str]] = (
        [("people", "People"), ("repository", "Collections / Institutions"),
         ("annotations", "Tags & data attributes"),
         ("relationships", "Biological relationships")]
        + [(spec.key, spec.title) for spec in VOCAB_REGISTRY]
    )
```

and after the `repository` tab panel add

```python
        # ── Tags & data attributes / Biological relationships (TaxonWorks-linkable) ──
        with ui.tab_panel("annotations").classes("p-0"):
            build_annotation_terms_section(session_factory)
        with ui.tab_panel("relationships").classes("p-0"):
            build_relationships_section(session_factory)
```

- [ ] **Step 7: Run the tests and an import smoke check**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_chips.py`
Expected: PASS.
Run (repo root): `conda run -n collection python -c "import app.ui.controlled_vocab_tab, app.ui.tw_vocab_section"`
Expected: no output, exit 0.

- [ ] **Step 8: Commit**

```bash
git add app/ui/merge_target_list.py app/ui/annotation_chips.py app/ui/tw_vocab_section.py \
  app/ui/controlled_vocab_tab.py tests/test_annotation_chips.py
git commit -m "Controlled Vocabularies: Tags & data attributes + Biological relationships sub-tabs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Annotation button + popup; annotations on the record sheets

**Files:**
- Create: `app/ui/annotation_panel.py`
- Modify: `app/ui/record_sheet.py` (`build_specimen_sheet`, `build_event_sheet`,
  `_render_specimen`, `_render_event`, `_ecology_block`)
- Create: `tests/test_annotation_panel_items.py`

**Interfaces:**
- Consumes: `annotations.*` (Task 2), `annotation_chips.*` (Task 8).
- Produces (used by Task 10):
  - `annotation_panel.collect_items(work: dict[int, str | None], terms: dict[int, dict])
    -> list[dict]` — pure; raises `ValueError` naming the term
  - `annotation_panel.build_annotation_button(session_factory, *, target,
    target_id_getter=None, staged=False, staged_store=None, deferred=False,
    on_change=None, tooltip="Annotations") -> dict` with keys `button`, `refresh`,
    `commit(session, target_id)`, `clear()`, `has_changes()`, `has_content()`,
    `staged_items`. Staged items are `{"term_id": int, "value": str | None}`.

- [ ] **Step 1: Write the failing test for the pure helper**

Create `tests/test_annotation_panel_items.py`:

```python
"""The popup's Save turns its working copy into items, refusing an unusable value by
name rather than dropping it (CLAUDE.md §2)."""
import pytest

from app.ui.annotation_panel import collect_items

TERMS = {1: {"kind": "tag", "name": "Adventive"},
         2: {"kind": "data_attribute", "name": "EUNIS habitat"}}


def test_collect_items_cleans_values():
    assert collect_items({1: None, 2: " G1.6 "}, TERMS) == [
        {"term_id": 1, "value": None}, {"term_id": 2, "value": "G1.6"}]


def test_collect_items_names_the_term_with_a_bad_value():
    with pytest.raises(ValueError, match="EUNIS habitat"):
        collect_items({2: "  "}, TERMS)


def test_collect_items_refuses_an_unknown_term():
    with pytest.raises(ValueError, match="no longer exists"):
        collect_items({9: None}, TERMS)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_panel_items.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ui.annotation_panel'`.

- [ ] **Step 3: Write `annotation_panel.py`**

```python
"""Annotation button + popup (spec "Entering annotations").

``build_annotation_button(...)`` renders a compact ``sell`` icon button with a count
badge that opens a popup: allowed tags as checkboxes, data attributes as pick + value.
Same three modes as ``external_id_panel.py``:

- **staged** (Digitize / Mounting): held in ``staged_store``; ``commit`` ADDS them on Save
  (``annotations.add_annotations`` — a reused event keeps what it has; a contradicting
  value refuses the Save).
- **deferred** (Records cards): edits held until the card's Save; ``commit`` REPLACES the
  record's set (``annotations.set_annotations``).
- **bound** (Records association rows, like the media / identifier buttons beside them):
  "Save & close" writes at once.

A term no longer offered for this target still shows when attached (marked) and can be
removed, but not newly added. Local-only terms carry a "local" badge.
"""
from __future__ import annotations

from typing import Callable, Optional

from nicegui import ui

import app.services.annotations as ann_svc
from app.services.annotations import AnnotationItem
from app.ui.annotation_chips import CSS as CHIP_CSS, chip_html


def collect_items(work: dict, terms: dict) -> list[dict]:
    """Working copy ``{term_id: value}`` -> items, in a stable order (tags first)."""
    items = []
    for term_id in sorted(work, key=lambda i: (terms.get(i, {}).get("kind") != "tag", i)):
        t = terms.get(term_id)
        if t is None:
            raise ValueError(f"An annotation refers to term {term_id}, which no longer "
                             f"exists — remove it and add it again.")
        value = work[term_id]
        if t["kind"] == "data_attribute":
            try:
                value = ann_svc.clean_value(value)
            except ValueError as exc:
                raise ValueError(f"{t['name']}: {exc}") from exc
        else:
            value = None
        items.append({"term_id": term_id, "value": value})
    return items


def _as_items(items: list[dict]) -> list[AnnotationItem]:
    return [AnnotationItem(i["term_id"], i["value"]) for i in items]


def build_annotation_button(
    session_factory,
    *,
    target: str,
    target_id_getter: Optional[Callable[[], Optional[int]]] = None,
    staged: bool = False,
    staged_store: Optional[list] = None,
    deferred: bool = False,
    on_change: Optional[Callable[[], None]] = None,
    tooltip: str = "Annotations",
) -> dict:
    ui.add_head_html(CHIP_CSS)
    staged_items: list[dict] = staged_store if staged_store is not None else []
    pending: dict = {"items": None}          # deferred: None = untouched since load

    def _tid() -> Optional[int]:
        return target_id_getter() if target_id_getter else None

    def _db_items() -> list[dict]:
        tid = _tid()
        if tid is None:
            return []
        with session_factory() as s:
            return [{"term_id": a.term_id, "value": a.value}
                    for a in ann_svc.annotations_for(s, target, tid)]

    def _current() -> list[dict]:
        if staged:
            return list(staged_items)
        if deferred and pending["items"] is not None:
            return list(pending["items"])
        return _db_items()

    btn = ui.button(icon="sell", on_click=lambda: _open()).props("flat dense round") \
        .tooltip(tooltip)
    with btn:
        badge = ui.badge("0", color="secondary").props("floating")
    badge.set_visibility(False)

    def refresh() -> None:
        n = len(_current())
        badge.set_text(str(n))
        badge.set_visibility(n > 0)
        btn.props(f'color={"secondary" if n else "grey"}')

    def _apply(items: list[dict]) -> bool:
        if staged:
            staged_items[:] = items
        elif deferred:
            pending["items"] = items
        else:
            tid = _tid()
            if tid is None:
                ui.notify("Save the record first.", type="warning")
                return False
            try:
                with session_factory() as s:
                    with s.begin():
                        ann_svc.set_annotations(s, target, tid, _as_items(items))
            except ValueError as exc:
                ui.notify(str(exc), type="warning", multi_line=True, timeout=8000)
                return False
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return False
        refresh()
        if on_change:
            on_change()
        return True

    def _open() -> None:
        current = _current()
        with session_factory() as s:
            allowed = {t.id for t in ann_svc.allowed_terms(s, target)}
            wanted = allowed | {i["term_id"] for i in current}
            terms = {t.id: {"kind": t.kind, "name": t.name, "css_color": t.css_color,
                            "local": t.tw_id is None, "allowed": t.id in allowed}
                     for t in ann_svc.list_terms(s) if t.id in wanted}
        work = {i["term_id"]: i["value"] for i in current}

        with ui.dialog() as dlg, ui.card().classes("min-w-[480px] gap-2"):
            ui.label(tooltip).classes("text-base font-medium")
            if target == "association":
                ui.label("Association annotations stay local — TaxonWorks cannot import "
                         "biological associations.").classes("text-xs") \
                    .style("color:var(--tp-base-soft)")
            ui.label("Tags").classes("text-sm font-medium")
            tags = sorted((i for i, t in terms.items() if t["kind"] == "tag"),
                          key=lambda i: terms[i]["name"].casefold())
            if not tags:
                ui.label("No tags are offered here — add or allow them in Controlled "
                         "Vocabularies.").classes("text-xs italic") \
                    .style("color:var(--tp-base-soft)")
            for tid in tags:
                t = terms[tid]
                with ui.row().classes("items-center gap-2"):
                    cb = ui.checkbox(value=tid in work)
                    if not t["allowed"] and tid not in work:
                        cb.disable()
                    cb.on_value_change(lambda e, i=tid: work.__setitem__(i, None)
                                       if e.value else work.pop(i, None))
                    ui.html(chip_html(t["name"], t["css_color"], local_only=t["local"]))
                    if not t["allowed"]:
                        ui.label("no longer offered").classes("text-xs") \
                            .style("color:var(--tp-base-soft)")

            ui.separator().classes("my-1")
            ui.label("Data attributes").classes("text-sm font-medium")
            da_box = ui.column().classes("w-full gap-1")

            def _render_da() -> None:
                da_box.clear()
                with da_box:
                    for tid in [i for i in work if terms.get(i, {}).get("kind") == "data_attribute"]:
                        t = terms[tid]
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.html(chip_html(t["name"], t["css_color"], local_only=t["local"]))
                            if not t["allowed"]:
                                ui.label("no longer offered").classes("text-xs") \
                                    .style("color:var(--tp-base-soft)")
                            v = ui.input(value=work[tid] or "").props("dense").classes("flex-1")
                            v.on_value_change(lambda e, i=tid: work.__setitem__(i, e.value))
                            ui.button(icon="delete",
                                      on_click=lambda _, i=tid: (work.pop(i, None), _render_da())) \
                                .props("flat dense round size=sm color=grey").tooltip("Remove")
                    free = {i: t["name"] for i, t in terms.items()
                            if t["kind"] == "data_attribute" and t["allowed"] and i not in work}
                    if free:
                        with ui.row().classes("items-end gap-2 w-full no-wrap"):
                            pick = ui.select(free, label="Add data attribute") \
                                .props("dense clearable").classes("w-48")
                            val = ui.input("Value").props("dense").classes("flex-1")

                            def _add_da() -> None:
                                if pick.value is None:
                                    ui.notify("Pick a data attribute first.", type="warning")
                                    return
                                work[pick.value] = val.value
                                _render_da()
                            ui.button(icon="add", on_click=_add_da) \
                                .props("flat dense round color=secondary").tooltip("Add")

            _render_da()

            def _save_close() -> None:
                try:
                    items = collect_items(work, terms)
                except ValueError as exc:
                    ui.notify(str(exc), type="warning", multi_line=True)
                    return
                if _apply(items):
                    dlg.close()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abort", on_click=dlg.close).props("flat")
                ui.button("Save & close", on_click=_save_close).props("color=secondary")
        # #65: rebuilt per open, so delete on close or its timers leak.
        dlg.on_value_change(lambda e: dlg.delete() if not e.value else None)
        dlg.open()

    def commit(session, target_id: int) -> None:
        """staged: ADD on the new / reused record. deferred: REPLACE the set."""
        if staged:
            if staged_items:
                ann_svc.add_annotations(session, target, target_id, _as_items(staged_items))
            return
        if deferred and pending["items"] is not None:
            ann_svc.set_annotations(session, target, target_id, _as_items(pending["items"]))
            pending["items"] = None

    def has_changes() -> bool:
        if not deferred or pending["items"] is None:
            return False
        key = lambda items: sorted((i["term_id"], i["value"] or "") for i in items)
        return key(pending["items"]) != key(_db_items())

    def clear() -> None:
        staged_items.clear()
        pending["items"] = None
        refresh()

    refresh()
    return {
        "button": btn, "refresh": refresh, "commit": commit, "clear": clear,
        "has_changes": has_changes,
        "has_content": (lambda: bool(staged_items)) if staged else (lambda: bool(_current())),
        "staged_items": staged_items,
    }
```

- [ ] **Step 4: Show annotations on the record sheets**

In `app/ui/record_sheet.py`:
- imports: `import app.services.annotations as ann_svc` and
  `from app.ui.annotation_chips import CSS as CHIP_CSS, annotations_html`.
- both `build_specimen_sheet` and `build_event_sheet`: add `ui.add_head_html(CHIP_CSS)`
  beside the existing `ui.add_head_html(CSS)`.
- `build_specimen_sheet`, inside the session, after `ext_ids = [...]`:

```python
        annotations = {
            "Specimen": annotations_html(ann_svc.views_for(s, "specimen", co_id)),
            "Event": annotations_html(ann_svc.views_for(s, "event", ev.id)) if ev else "",
        }
```
  and inside the association loop add `"annotations": annotations_html(
  ann_svc.views_for(s, "association", a.id))` to each `assocs.append({...})` dict. Pass
  `annotations=annotations` to `_render_specimen` (new keyword-only parameter
  `annotations: dict | None = None`).
- `_render_specimen`: in the right column, after the "In the collection" card, add

```python
            shown = {k: v for k, v in (annotations or {}).items() if v}
            if shown:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Annotations</div>')
                    for label, html in shown.items():
                        ui.html(f'<div class="rsheet-muted">{label}</div>{html}')
```
- `_ecology_block`: append `{a.get("annotations", "")}` after `{links}` inside the
  association's `rsheet-det` div.
- `build_event_sheet`: inside the session add
  `ann_html = annotations_html(ann_svc.views_for(s, "event", ev_id))`, pass
  `annotations=ann_html` to `_render_event` (new keyword-only parameter
  `annotations: str = ""`), and in `_render_event`'s right column, before the "Details"
  card:

```python
            if annotations:
                with ui.card().classes("w-full shadow-sm"):
                    ui.html('<div class="rsheet-hd">Annotations</div>')
                    ui.html(annotations)
```

- [ ] **Step 5: Run the tests and an import check**

Run: `conda run -n collection python -m pytest -q tests/test_annotation_panel_items.py`
Expected: PASS.
Run (repo root): `conda run -n collection python -c "import app.ui.annotation_panel, app.ui.record_sheet"`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add app/ui/annotation_panel.py app/ui/record_sheet.py tests/test_annotation_panel_items.py
git commit -m "Annotations: footer button + popup (staged/deferred/bound); chips on record sheets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Wire the button into Digitize, Mounting and Records

**Files:**
- Modify: `app/ui/digitize_tab.py`
- Modify: `app/ui/mounting_session.py`
- Modify: `app/ui/records_tab.py`

**Interfaces:**
- Consumes: `annotation_panel.build_annotation_button` (Task 9);
  `annotations.add_annotations`, `AnnotationItem` (Task 2).
- Produces: nothing new; every create/edit path now carries annotations inside its one
  atomic Save (CLAUDE.md §6).

There is no unit test for this wiring — CLAUDE.md requires verifying UI by driving the real
forms, which is Task 12. Keep each edit exactly as below; run the full suite after.

- [ ] **Step 1: Digitize — specimen cards (standard + visiting)**

In `app/ui/digitize_tab.py`:
- imports: `from app.ui.annotation_panel import build_annotation_button` and
  `import app.services.annotations as ann_svc`.
- beside `spec_ls = {}` / `spec_ls_v = {}` add `spec_ann = {}` and `spec_ann_v = {}`;
  beside `_sl_items, _sl_items_v = [], []` add
  `_sa_items, _sa_items_v = [], []          # specimen annotations`.
- `_mk_spec_footer` gains two parameters and one button:

```python
        def _mk_spec_footer(media_holder, extid_holder, ls_holder, ann_holder,
                            media_store, extid_store, ls_store, ann_store):
            # Staged controllers, rendered bottom-right of the specimen card.
            ls_holder.update(build_life_stage_button(
                _sf, staged=True, staged_store=ls_store))
            ann_holder.update(build_annotation_button(
                _sf, target="specimen", staged=True, staged_store=ann_store,
                tooltip="Specimen annotations (added on Save)"))
            extid_holder.update(build_external_id_button(
                _sf, target_kind="collection_object", staged=True,
                staged_store=extid_store,
                tooltip="Specimen resource identifiers (attached on Save)"))
            media_holder.update(build_media_button(
                _sf, target_kind="collection_object", staged=True,
                staged_store=media_store,
                tooltip="Specimen media (attached on Save)"))
```

  and both call sites become
  `_mk_spec_footer(spec_media, spec_extid, spec_ls, spec_ann, _sm_items, _se_items, _sl_items, _sa_items)`
  and
  `_mk_spec_footer(spec_media_v, spec_extid_v, spec_ls_v, spec_ann_v, _sm_items_v, _se_items_v, _sl_items_v, _sa_items_v)`.
- after `_active_ls`:

```python
        def _active_ann() -> dict:
            """The staged annotation controller for the active specimen card."""
            return spec_ann_v if _active_spec[0] is spec_visiting else spec_ann
```

- [ ] **Step 2: Digitize — event card and association rows**

- beside `event_media: dict = {}` add `event_ann: dict = {}` and `_ea_items: list = []`;
  `_event_footer` becomes

```python
            def _event_footer():
                event_media.update(build_media_button(
                    _sf, target_kind="collecting_event", staged=True,
                    staged_store=_em_items,
                    tooltip="Event media (attached on Save)"))
                event_ann.update(build_annotation_button(
                    _sf, target="event", staged=True, staged_store=_ea_items,
                    tooltip="Event annotations (added on Save — also to a reused event)"))
```

  (the event form already disables every footer button while the event is read-only,
  `collecting_event_form.py` `_footer_box`).
- in `_refresh_assoc_list`, beside `a.setdefault("extid_items", [])` add
  `a.setdefault("annotation_items", [])`, and before the `build_external_id_button(...)` call:

```python
                            build_annotation_button(
                                _sf, target="association", staged=True,
                                staged_store=a["annotation_items"],
                                tooltip="Association annotations (on Save; stay local)")
```

- [ ] **Step 3: Digitize — Save, unsaved-content check, wipes**

- in the Save transaction, after `_active_ls()["commit"](session, co.id)`:

```python
                        _active_ann()["commit"](session, co.id)
```
  and change the event block to

```python
                        if co.collecting_event_id:
                            event_media["commit"](session, co.collecting_event_id)
                            event_ann["commit"](session, co.collecting_event_id)
```
  and inside `for _assoc, _ba in zip(bio_state["associations"], created_assocs):` add first

```python
                            if _assoc.get("annotation_items"):
                                ann_svc.add_annotations(
                                    session, "association", _ba.id,
                                    [ann_svc.AnnotationItem(i["term_id"], i["value"])
                                     for i in _assoc["annotation_items"]])
```
  These run inside `with session.begin():`, so a refused value (Review Focus 3) rolls the
  whole specimen back and reaches the existing `except Exception as exc: ui.notify(f"Save
  failed: {exc}")` — the message names both values.
- after the post-save `spec_ls["clear"](); spec_ls_v["clear"]()` line add
  `spec_ann["clear"](); spec_ann_v["clear"](); event_ann["clear"]()`; add the same line
  after `spec_ls["clear"](); spec_ls_v["clear"]()` in the mode-toggle wipe.
- in the unsaved-content check (the `or …` chain ending in
  `any(a.get("media_items") or a.get("extid_items") for a in bio_state["associations"])`):
  add `or _active_ann()["has_content"]()` and `or event_ann["has_content"]()`, and extend the
  `any(...)` to `a.get("media_items") or a.get("extid_items") or a.get("annotation_items")`.
- `_ms_on_saved` (Mounting saved): add `event_ann["clear"]()` as its first line — the
  mounting save commits event annotations (Step 4), so they must not stay staged.
- pass the event-annotation commit into the mounting section:
  `commit_event_annotations=lambda s, ev_id: event_ann["commit"](s, ev_id),` in the
  `build_mounting_session_section(...)` call.

- [ ] **Step 4: Mounting session**

In `app/ui/mounting_session.py`:
- imports: `from app.ui.annotation_panel import build_annotation_button` and
  `import app.services.annotations as ann_svc`.
- `_empty_row()` gains `"annotations": [],`.
- `build_mounting_session_section` gains the keyword parameter
  `commit_event_annotations=None,   # (session, event_id) -> None: the shared event card's staged annotations`.
- in `_rebuild_table`, just before `ui.space()`:

```python
                    build_annotation_button(
                        session_factory, target="specimen", staged=True,
                        staged_store=row.setdefault("annotations", []),
                        tooltip="Annotations for this row's specimen (on Save)")
```
- in `_do_save`, replace `svc.finalize_specimen(` … `)` with `created = svc.finalize_specimen(` …
  `)` (arguments unchanged) and add right after it:

```python
                        if row.get("annotations"):
                            ann_svc.add_annotations(
                                s, "specimen", co.id,
                                [ann_svc.AnnotationItem(i["term_id"], i["value"])
                                 for i in row["annotations"]])
                        for assoc, ba in zip(bio_state["associations"], created):
                            if assoc.get("annotation_items"):
                                ann_svc.add_annotations(
                                    s, "association", ba.id,
                                    [ann_svc.AnnotationItem(i["term_id"], i["value"])
                                     for i in assoc["annotation_items"]])
```
  and after the `for row, code in zip(rows, codes):` loop (still inside `with s.begin():`):

```python
                    if commit_event_annotations is not None and event_id is not None:
                        commit_event_annotations(s, event_id)
```
- `has_content()`: add `or bool(r.get("annotations"))` to the returned expression.

- [ ] **Step 5: Records — specimen card, its event, association rows, event page**

In `app/ui/records_tab.py`:
- import `from app.ui.annotation_panel import build_annotation_button`.
- `_build_spec_footer`: add before `return (`

```python
            _sub["ann"] = build_annotation_button(
                session_factory, target="specimen", target_id_getter=lambda: co_id,
                deferred=True, tooltip="Specimen annotations")
```
  and add `_sub["ann"]["button"],` as the first element of the returned tuple.
- in `_save`'s transaction, after `_sub["ext"]["commit"](s, co_id)`: `_sub["ann"]["commit"](s, co_id)`.
- the dirty lambda (`_dirty["fn"] = lambda: (_current_values() != _baseline …`): add
  `or _sub["ann"]["has_changes"]()`.
- the specimen page's event footer (`def _ev_footer():` inside the specimen form, where
  `if ev_id:` builds the media button): add

```python
                    if ev_id:
                        _ev_ann["h"] = build_annotation_button(
                            session_factory, target="event",
                            target_id_getter=lambda: ev_id, deferred=True,
                            tooltip="Event annotations")
```
  with `_ev_ann: dict = {"h": None}` declared just above `def _ev_footer():`. In `_save`,
  inside the existing `if ev_fields and ev_id and _ev_editable[0]:` block, after
  `ev_svc.update_collecting_event(...)`, add
  `if _ev_ann["h"] is not None: _ev_ann["h"]["commit"](s, ev_id)` — the event is only
  written when unlocked, and the form disables the footer button while it is locked. Add
  `or (_ev_ann["h"] is not None and _ev_ann["h"]["has_changes"]())` to the dirty lambda.
- association rows (`_refresh_assoc_list`, inside `if a["id"] is not None:`, before the
  `_media_btn(...)` call): bound mode, like its neighbours —

```python
                                build_annotation_button(
                                    session_factory, target="association",
                                    target_id_getter=lambda aid=a["id"]: aid,
                                    tooltip="Association annotations (stay local)")
```
  and change the staged-row tooltip to "Saved with the specimen — attach media, a resource
  identifier or annotations afterwards".
- the event page (`_build_event_form`): declare `_ev_ann: dict = {"h": None}` before its
  `_ev_footer`, build `_ev_ann["h"] = build_annotation_button(session_factory,
  target="event", target_id_getter=lambda: ev_id, deferred=True, tooltip="Event
  annotations")` inside it, call `_ev_ann["h"]["commit"](s, ev_id)` in `_save_event` after
  `ev_svc.update_collecting_event(...)`, and change the dirty lambda to
  `lambda: _ev_values() != _ev_baseline["v"] or _ev_ann["h"]["has_changes"]()`.

- [ ] **Step 6: Run the suite and an import check**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS.
Run (repo root): `conda run -n collection python -c "import app.ui.digitize_tab, app.ui.mounting_session, app.ui.records_tab"`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add app/ui/digitize_tab.py app/ui/mounting_session.py app/ui/records_tab.py
git commit -m "Annotations: Digitize (standard/visiting/mounting), Records specimen/event/association

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: TaxonWorks tab — export summary, term pre-flight gate, Compare section

**Files:**
- Modify: `app/ui/tw_sync_tab.py`

**Interfaces:**
- Consumes: `ExportResult.annotation_*` (Task 5); `annotation_export.check_terms`,
  `TermCheck` (Task 6); `tw_annotation_compare.run_annotation_compare`,
  `AnnotationCompareResult` (Task 7).
- Produces: nothing new.

- [ ] **Step 1: Run the term pre-flight in "Check collection"**

In `_check_collection`, inside the second `with session_factory() as s:` block, after
`checks = await tw_sync.check_names(s, taxa_list)`:

```python
                            # Term pre-flight (spec "Term pre-flight"): beside the name
                            # pre-flight; nothing exports until both pass.
                            term_checks = (
                                await annotation_export.check_terms(result.annotation_columns)
                                if result.annotation_columns and not result.annotation_blocked
                                else [])
```
and after `state["checks"] = checks` add `state["term_checks"] = term_checks`. Initialise
`"term_checks": None` wherever `state` is created (beside `"checks": None`). Import
`from app.services import annotation_export` and
`import app.services.tw_annotation_compare as tw_annotation_compare` at the top.

- [ ] **Step 2: Gate the Download**

Replace `_sync_download_enabled` with:

```python
                def _sync_download_enabled() -> None:
                    checks = state["checks"]
                    result = state["result"]
                    if checks is None or result is None:
                        download_btn.set_enabled(False)
                        download_tip.set_text("Run Check collection first")
                        return
                    blocking_n = sum(1 for c in checks if c.blocks_export)
                    term_n = (len(result.annotation_blocked)
                              + sum(1 for c in (state["term_checks"] or []) if not c.ok))
                    if blocking_n or term_n:
                        download_btn.set_enabled(False)
                        reasons = []
                        if blocking_n:
                            reasons.append(f"{blocking_n} name(s) are not on TaxonWorks yet")
                        if term_n:
                            reasons.append(f"{term_n} annotation term(s) cannot be exported")
                        download_tip.set_text(" · ".join(reasons))
                    else:
                        download_btn.set_enabled(True)
                        download_tip.set_text("Download the spreadsheet")
```

- [ ] **Step 3: Show the annotation part of the export summary**

In `_render_results`, after `_render_blocking_names(checks)`:

```python
                        # ── annotation terms — blockers first, then information ──
                        bad_terms = list(result.annotation_blocked) + [
                            (c.local_name, c.reason) for c in (state["term_checks"] or [])
                            if not c.ok]
                        if bad_terms:
                            ui.label(f"Annotation terms blocking export ({len(bad_terms)})") \
                                .classes("text-sm font-semibold text-red-600 mt-3")
                            for name, reason in bad_terms:
                                ui.label(f"{name} — {reason}").classes("text-xs")
                            ui.label("Fix them in Controlled Vocabularies → Tags & data "
                                     "attributes (Verify), then run Check again.") \
                                .classes("text-xs text-red-600")
                        elif result.annotation_columns:
                            ui.label(
                                f"{len(result.annotation_columns)} annotation column(s): "
                                + ", ".join(c.header for c in result.annotation_columns)
                            ).classes("text-xs mt-2").style("color:var(--tp-base-soft)")
                        if result.annotation_local_only:
                            ui.label(
                                "Local-only annotations (not exported): "
                                + "; ".join(f"{n} on {k} specimen(s)"
                                            for n, k in result.annotation_local_only)
                            ).classes("text-xs mt-1").style("color:var(--tp-base-soft)")
```

- [ ] **Step 4: Say what cannot be exported**

In the static "What cannot be exported" list add the line
`"Annotations on biological associations, and annotation terms that are local only.",`.

- [ ] **Step 5: Run the annotation compare in the per-collection Check**

In `_on_check_repo`, after the media block (`except tw_svc.TaxonWorksUnreachable as
media_exc: …`), add — isolated in its own try like media (#162):

```python
                        _status(f"Checking {row['collection']} — comparing annotations…")
                        ann_result = None
                        try:
                            with session_factory() as s:
                                ann_result = await tw_annotation_compare.run_annotation_compare(
                                    s, repository_id=repo_id, index=index)
                        except tw_svc.TaxonWorksUnreachable as ann_exc:
                            ui.notify(
                                f"Annotation comparison failed ({ann_exc}) — the occurrence, "
                                f"name and media results below are unaffected.",
                                type="warning", multi_line=True, timeout=8000,
                            )
```
and add `"annotations": ann_result,` to the `state["compare"][repo_id] = {...}` dict.

- [ ] **Step 6: Render the annotation findings**

Add next to `_render_media_results`:

```python
    _ANN_CATEGORY = {
        "missing_on_tw": "Missing on TaxonWorks — add it there by hand (an export cannot "
                         "update a record already uploaded)",
        "only_on_tw": "Only on TaxonWorks",
        "value_differs": "Value differs",
        "unlinked_on_tw": "On TaxonWorks under a term not linked here",
    }

    def _render_annotation_results(ann_result) -> None:
        """Spec "Compare": diagnostic only — annotation divergence cannot be repaired by
        exporting again (CREATE-ONLY)."""
        if ann_result is None:
            ui.label("Annotations were not checked.").classes("text-xs mt-2") \
                .style("color:var(--tp-base-soft)")
            return
        ui.label("Annotations").classes("text-sm font-semibold mt-3")
        if not ann_result.findings:
            ui.label(f"No differences across {ann_result.checked_count} specimen(s) on "
                     f"TaxonWorks.").classes("text-xs").style("color:var(--tp-base-soft)")
        for category, title in _ANN_CATEGORY.items():
            rows = [f for f in ann_result.findings if f.category == category]
            if not rows:
                continue
            with ui.expansion(f"{title} ({len(rows)})").classes("w-full"):
                for f in rows:
                    vals = " · ".join(x for x in (
                        f"here: {f.local_value}" if f.local_value else "",
                        f"TaxonWorks: {f.tw_value}" if f.tw_value else "") if x)
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{id_svc.catalog_label(f.catalog_number)} · {f.target} · "
                                 f"{f.name}" + (f" — {vals}" if vals else "")) \
                            .classes("text-xs")
                        # Both ends, like `diverged` rows (spec "Compare"): the local record
                        # via the same Explore hand-off the other result groups use, and
                        # the TW object in its comprehensive editor.
                        ui.button("show here", icon="open_in_new",
                                  on_click=lambda _, c=f.catalog_number: _open_catalog_numbers(
                                      [c], f"Annotation difference — {c}")) \
                            .props("flat dense no-caps size=sm")
                        ui.link("open in TaxonWorks", tw_compare.edit_url(f.tw_object_id),
                                new_tab=True).classes("text-xs")
        if ann_result.unverified_terms:
            ui.label("Not compared — link not verified on this instance: "
                     + ", ".join(ann_result.unverified_terms)).classes("text-xs mt-1") \
                .style("color:var(--tp-base-soft)")
        if ann_result.ambiguous_catalog_numbers:
            ui.label("Not compared — catalog number held by more than one TaxonWorks "
                     "record: " + ", ".join(ann_result.ambiguous_catalog_numbers)) \
                .classes("text-xs mt-1").style("color:var(--tp-base-soft)")
        if ann_result.associations_not_compared:
            ui.label("Annotations on biological associations are not compared — "
                     "TaxonWorks cannot import associations.").classes("text-xs mt-1") \
                .style("color:var(--tp-base-soft)")
```

and call `_render_annotation_results(data.get("annotations"))` right after
`_render_media_results(data.get("media"))` in `_render_compare_results`.

- [ ] **Step 7: Run the suite and an import check**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS.
Run (repo root): `conda run -n collection python -c "import app.ui.tw_sync_tab"`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add app/ui/tw_sync_tab.py
git commit -m "TaxonWorks tab: annotation columns, term pre-flight gate, annotation compare

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Docs, full verification in the real UI, hand-off

**Files:**
- Modify: `docs/app_structure.md` (service-layer module table: `annotations.py`,
  `tw_links.py`, `annotation_export.py`, `tw_annotation_compare.py`; UI: `annotation_panel.py`,
  `annotation_chips.py`, `tw_vocab_section.py`, `merge_target_list.py`)
- Modify: `docs/design.md` (one short section "Annotation chips": colour rule, black/white
  text by luminance, neutral chip, "local" badge, `sell` footer icon)
- Modify: `CLAUDE.md` (§4: one line — "Annotations: local `annotation_term` vocabulary with an
  optional ONE TaxonWorks link; `tw_host` is earned by a fetch, never assumed;
  `tw_links.py` is the single owner of link / verify for terms and relationships.")
- Modify: `docs/superpowers/specs/2026-10-14-annotations-design.md` (Status: "implemented
  on `feature/annotations`")

- [ ] **Step 1: Write the docs** as listed above, each in its file's existing style; no
  detail duplicated between files (CLAUDE.md ownership rule).

- [ ] **Step 2: Full suite**

Run: `conda run -n collection python -m pytest -q`
Expected: PASS, test count up by the new files.

- [ ] **Step 3: Ask the user before any UI verification**

Ask: "May I copy `data/collection.db` to a scratch folder and drive the app against the
copy with Playwright on port 8091? The copy gets migration 0073 applied; the real DB is
not touched." Wait for an explicit yes. Do not start a server otherwise.

- [ ] **Step 4: Drive the real forms (only after the yes)**

Per `reference_gui_verification` memory: spare port, the copy's path via the app's DB
setting, screenshot and read each step. Cover:
1. Controlled Vocabularies → Tags & data attributes: add local tag "Adventive" (specimens);
   Add from TaxonWorks (sfg keyword `Adventive`, id 1764) — the chip reads
   `TaxonWorks ✓`, the colour matches TW; edit colour is read-only while linked.
2. Verify with nothing changed → all confirmed. Biological relationships sub-tab: existing
   linked rows read `not verified` until Verify.
3. Digitize standard: tag the specimen, add an event data attribute, save — Records sheet
   shows both annotation lines; reopen the specimen, remove the tag, Save changes.
4. Digitize with a reused event that already has `EUNIS habitat = G1.6`, stage `G1.7` →
   Save refused naming both values; nothing saved.
5. Mounting: two rows, one annotated → only that specimen carries it.
6. TaxonWorks tab → Export → Check collection: the annotation column is listed; with the
   host switched to the sandbox in Settings the Download is blocked with the reason.
7. Stop the server; verify with `pgrep -af run.py` and `ss -ltnp | grep 8091` that nothing
   is left running; delete the scratch copy.

- [ ] **Step 5: Commit the docs**

```bash
git add docs/app_structure.md docs/design.md CLAUDE.md docs/superpowers/specs/2026-10-14-annotations-design.md
git commit -m "Docs: annotations — module table, chip design, CLAUDE.md rule, spec status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hand off** with `superpowers:finishing-a-development-branch` (PR into
  `main`; the user decides merge vs. PR).
