"""Tests for the startup data-safety checks (app/services/db_safety.py).

Covers the three steps run at launch — checkpoint/integrity/snapshot — plus the
two outcomes that matter most: a healthy file is reported ok with a fresh
snapshot, and a corrupted file is reported NOT ok so the UI banner fires.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, text

import app.services.db_safety as db_safety


def _make_db(path, *, rows: int = 3) -> "create_engine":
    """A valid WAL-mode SQLite file at *path*; return an engine for it.

    ``rows`` controls size — pass a large value to force a multi-page file with
    real b-tree/index pages (needed to make corruption detectable)."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode = WAL")
        conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.exec_driver_sql("CREATE INDEX ix_t_v ON t (v)")
        for i in range(rows):
            conn.exec_driver_sql("INSERT INTO t (v) VALUES (:v)", {"v": f"value-{i:06d}"})
        conn.commit()
    return engine


def test_integrity_check_passes_on_healthy_db(tmp_path):
    engine = _make_db(tmp_path / "c.db")
    assert db_safety.integrity_check(engine) == []
    engine.dispose()


def test_snapshot_creates_copy_and_prunes(tmp_path):
    engine = _make_db(tmp_path / "c.db")
    db_safety.checkpoint(engine)
    # Make more snapshots than we keep; only the newest `keep` survive.
    paths = []
    for _ in range(4):
        paths.append(db_safety.snapshot(engine, keep=2))
    snap_dir = tmp_path / "snapshots"
    survivors = sorted(snap_dir.glob("c-*.db"))
    assert len(survivors) == 2
    # Each snapshot is itself a valid, queryable copy of the data.
    last = paths[-1]
    con = sqlite3.connect(last)
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    con.close()
    engine.dispose()


def test_run_startup_safety_ok_on_healthy_db(tmp_path):
    engine = _make_db(tmp_path / "c.db")
    res = db_safety.run_startup_safety(engine, keep=5)
    assert res.ok is True
    assert res.skipped is False
    assert res.snapshot_path is not None and res.snapshot_path.exists()
    assert db_safety.LAST_RESULT is res
    engine.dispose()


def test_run_startup_safety_skips_in_memory():
    engine = create_engine("sqlite://")  # :memory:
    res = db_safety.run_startup_safety(engine)
    assert res.skipped is True
    assert res.snapshot_path is None
    engine.dispose()


def test_run_startup_safety_detects_corruption(tmp_path):
    db_file = tmp_path / "c.db"
    engine = _make_db(db_file, rows=2000)  # multi-page file with real b-tree pages
    db_safety.checkpoint(engine)   # fold WAL in so all data is in the main file
    engine.dispose()               # release the file before we scribble on it

    # Scramble everything from page 2 onward, leaving page 1 (header + schema)
    # intact so SQLite still opens the file but the b-tree/index pages are broken.
    data = bytearray(db_file.read_bytes())
    assert len(data) > 4096, "DB should span multiple pages for this test"
    for i in range(4096, len(data)):
        data[i] = (data[i] + 137) % 256
    db_file.write_bytes(data)

    engine2 = create_engine(f"sqlite:///{db_file}")
    res = db_safety.run_startup_safety(engine2, keep=5)
    assert res.ok is False
    assert res.integrity_problems  # non-empty list of reported problems
    engine2.dispose()
