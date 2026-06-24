"""Permanent guard against silent loss of STRICT typing, CHECK constraints, and
FK ON DELETE actions.

This is the regression test for DB-1 (#1): migrations 0021/0024 (and 0020/0025-27)
rebuilt tables with ``batch_alter_table(recreate=...)``, which reflects columns but
silently drops STRICT, CHECK constraints, and FK ON DELETE actions. Migration 0029
restored them. These tests assert — against a freshly-migrated DB (the ``engine``
fixture runs ``alembic upgrade head``) — that they are still there, so any future
migration that drops one again fails the suite loudly.

Expectations are derived from the models (CHECKs, FK ON DELETE) plus an explicit
STRICT allow-list (STRICT cannot be expressed in a SQLAlchemy model, so its source
of truth is the original CREATE ... STRICT migration DDL).

**If you add a `recreate=` migration, it MUST re-declare STRICT, every CHECK, and
every FK ON DELETE action — see CLAUDE.md "Migration discipline".**
"""
import pytest

from app.models import Base

# Tables created with STRICT in the original migrations (0001/0002/0005/0007/0009/
# 0010/0012 + label/print-queue migrations). person / person_defaults were never STRICT.
STRICT_TABLES = sorted({
    "taxon", "collecting_event", "collection_object", "taxon_determination",
    "biological_association", "biological_relationship",
    "label_code", "label_batch", "print_queue",
    "media", "media_attachment",  # 0035
})

# Tables whose constraints were dropped + restored — checked in extra detail below.
_RESTORED = ["collecting_event", "collection_object", "taxon_determination",
             "label_code", "print_queue"]


def _table_sql(engine, table):
    with engine.connect() as conn:
        return conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).scalar()


def _model_check_names(table):
    tbl = Base.metadata.tables[table]
    return {c.name for c in tbl.constraints
            if c.__class__.__name__ == "CheckConstraint" and c.name}


def _model_fk_ondelete(table):
    """{local_column: expected ON DELETE action as SQLite reports it}."""
    tbl = Base.metadata.tables[table]
    return {fk.parent.name: (fk.ondelete or "NO ACTION").upper()
            for fk in tbl.foreign_keys}


@pytest.mark.parametrize("table", STRICT_TABLES)
def test_table_is_strict(engine, table):
    sql = _table_sql(engine, table)
    assert sql is not None, f"{table} does not exist"
    assert "STRICT" in sql.upper(), f"{table} lost its STRICT typing"


@pytest.mark.parametrize("table", STRICT_TABLES)
def test_check_constraint_count_not_reduced(engine, table):
    """Live table has at least as many CHECK clauses as the model declares.

    Count-based so it also covers tables whose CHECKs are unnamed in the DB
    (e.g. biological_association's exclusive-arc checks from migration 0007).
    """
    import re
    tbl = Base.metadata.tables[table]
    model_count = sum(1 for c in tbl.constraints
                      if c.__class__.__name__ == "CheckConstraint")
    if model_count == 0:
        pytest.skip(f"{table} declares no CHECK constraints in the model")
    sql = _table_sql(engine, table)
    live_count = len(re.findall(r"CHECK\s*\(", sql, flags=re.I))
    assert live_count >= model_count, (
        f"{table} has {live_count} CHECK clause(s), model declares {model_count}"
    )


@pytest.mark.parametrize("table", _RESTORED)
def test_named_checks_present_on_restored_tables(engine, table):
    """The DB-1 tables use *named* CHECKs — assert each model name is present."""
    expected = _model_check_names(table)
    assert expected, f"{table} unexpectedly declares no named CHECK constraints"
    sql = _table_sql(engine, table)
    missing = {name for name in expected if name not in sql}
    assert not missing, f"{table} lost CHECK constraint(s): {sorted(missing)}"


@pytest.mark.parametrize("table", STRICT_TABLES)
def test_fk_ondelete_actions_present(engine, table):
    expected = _model_fk_ondelete(table)
    if not expected:
        pytest.skip(f"{table} has no foreign keys")
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            f"PRAGMA foreign_key_list('{table}')"
        ).fetchall()
    # PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
    live = {r[3]: (r[6] or "NO ACTION").upper() for r in rows}
    for col, action in expected.items():
        assert live.get(col) == action, (
            f"{table}.{col} ON DELETE is {live.get(col)!r}, expected {action!r}"
        )


def _model_unique_colsets(table):
    tbl = Base.metadata.tables[table]
    sets = set()
    for con in tbl.constraints:
        if con.__class__.__name__ == "UniqueConstraint":
            sets.add(tuple(sorted(c.name for c in con.columns)))
    for col in tbl.columns:
        if col.unique:
            sets.add((col.name,))
    return sets


@pytest.mark.parametrize("table", STRICT_TABLES)
def test_unique_constraints_present(engine, table):
    """UNIQUE constraints survive table rebuilds.

    Regression for the bug *this migration itself* first introduced: generating
    0029 from the model dropped collection_object's UNIQUE(collectionCode,
    catalogNumber) because it was undeclared in the model. It is now declared,
    and this guards it (and label_code.code).
    """
    expected = _model_unique_colsets(table)
    if not expected:
        pytest.skip(f"{table} declares no UNIQUE constraints in the model")
    with engine.connect() as conn:
        live = set()
        for idx in conn.exec_driver_sql(f"PRAGMA index_list('{table}')").fetchall():
            if idx[2]:  # unique flag
                cols = conn.exec_driver_sql(f"PRAGMA index_info('{idx[1]}')").fetchall()
                live.add(tuple(sorted(c[2] for c in cols)))
    missing = expected - live
    assert not missing, f"{table} lost UNIQUE constraint(s) on column-set(s): {missing}"


def test_taxon_status_column_dropped(engine):
    # taxonomicStatus was dropped in migration 0030: synonymy is derived from
    # acceptedNameUsageID, never stored. Guard against accidental re-introduction
    # (a derived column that can drift is exactly what 0030 removed). STRICT and
    # the self-FK ON DELETE actions are covered by the generic tests above.
    sql = _table_sql(engine, "taxon")
    assert "taxonomicStatus" not in sql, \
        "taxon re-introduced the derived taxonomicStatus column (see migration 0030 / CLAUDE.md §4)"


def test_taxon_name_element_present(engine):
    # migration 0032: name_element is the atomic source of truth (the rank's own
    # epithet/uninomial); dwc:scientificName is the composed full name. Declared
    # TEXT so it satisfies STRICT. See Epic #30.
    sql = _table_sql(engine, "taxon")
    assert "name_element" in sql, "taxon lost the name_element column (migration 0032)"
    with engine.connect() as conn:
        col = next(
            r for r in conn.exec_driver_sql("PRAGMA table_info(taxon)")
            if r[1] == "name_element"
        )
    assert col[2].upper() == "TEXT", f"name_element must be TEXT for STRICT, got {col[2]!r}"


def test_synonym_integrity_triggers_present(engine):
    # migration 0031 created four triggers; migration 0033 retired the two
    # synonym-parent-match ones (atomic model parents synonyms under their own
    # lineage). The terminal (chained-synonym) triggers remain in force, and a
    # taxon table rebuild silently drops triggers — re-create them.
    expected = {
        "trg_taxon_accepted_is_terminal_ins",
        "trg_taxon_accepted_is_terminal_upd",
    }
    retired = {
        "trg_taxon_synonym_parent_matches_accepted_ins",
        "trg_taxon_synonym_parent_matches_accepted_upd",
    }
    with engine.connect() as conn:
        live = {r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='taxon'")}
    missing = expected - live
    assert not missing, f"taxon lost synonym-integrity trigger(s): {sorted(missing)}"
    still_present = retired & live
    assert not still_present, (
        f"retired synonym-parent-match trigger(s) still present: {sorted(still_present)} "
        "(migration 0033 should have dropped them)"
    )
