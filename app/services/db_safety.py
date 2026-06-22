"""Startup data-safety checks: WAL checkpoint, integrity verification, snapshot.

Run once at launch (see ``run.py``) on the live engine, *before* the UI serves
any page. Three steps, in order:

1. **Checkpoint the WAL** (``PRAGMA wal_checkpoint(TRUNCATE)``) so the main
   ``.db`` file holds the full committed state. This is required before both the
   snapshot copy and the integrity check — otherwise either could see a stale
   file (the WAL caveat documented in CLAUDE.md §8).
2. **Snapshot** the ``.db`` to ``data/snapshots/collection-<timestamp>.db`` and
   prune to the most recent ``keep`` copies. Recovery insurance: the integrity
   check only *detects* a damaged file; the snapshot is what lets you roll back
   to a recent good copy. Cheap at this project's scale (a single-collection DB
   is a few MB; 30 k specimens ≈ tens of MB).
3. **Integrity check** (``PRAGMA integrity_check``) — the thorough structural
   verification (B-tree, page, and index-vs-table cross-checks). Returns ``ok``
   on a sound file, otherwise a list of specific problems. On anything but
   ``ok`` we surface a loud, blocking banner in the UI rather than silently
   opening a damaged file and writing more into it (CLAUDE.md §2: a loud failure
   beats a silent wrong value).

The result is cached in ``LAST_RESULT`` so the ``@ui.page`` handler can read it
and render a banner without re-running the check per page load.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

log = logging.getLogger(__name__)

# Number of launch snapshots to retain (oldest pruned). Cheap at this scale.
DEFAULT_KEEP = 10


@dataclass
class DbSafetyResult:
    """Outcome of the startup checks. ``ok`` is False only on integrity failure."""
    ok: bool = True
    integrity_problems: list[str] = field(default_factory=list)
    snapshot_path: Path | None = None
    skipped: bool = False          # non-file DB (e.g. in-memory) — nothing to do
    error: str | None = None       # an unexpected failure running the checks


# Cached result of the last run, read by the UI to decide whether to warn.
LAST_RESULT: DbSafetyResult = DbSafetyResult(skipped=True)


def _db_path(engine) -> Path | None:
    """Filesystem path of a file-backed SQLite engine, or None (in-memory/other)."""
    if engine.url.get_backend_name() != "sqlite":
        return None
    db = engine.url.database
    if not db or db == ":memory:":
        return None
    return Path(db)


def checkpoint(engine) -> None:
    """Fold the WAL into the main .db file so it is self-contained on disk."""
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")


def integrity_check(engine) -> list[str]:
    """Run the thorough ``PRAGMA integrity_check``. Return [] if sound, else the
    list of reported problems.

    A badly damaged file ("database disk image is malformed") raises rather than
    returning problem rows — that exception *is* the corruption signal, so it is
    caught and reported as a problem (not allowed to escape as a soft error)."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("PRAGMA integrity_check")).fetchall()
    except Exception as exc:
        return [f"integrity_check could not run: {exc}"]
    problems = [r[0] for r in rows]
    if problems == ["ok"]:
        return []
    return problems


def snapshot(engine, *, keep: int = DEFAULT_KEEP) -> Path | None:
    """Copy the live .db to ``data/snapshots/collection-<timestamp>.db`` and prune
    to the most recent ``keep``. Assumes the WAL has been checkpointed first.
    Returns the snapshot path, or None when there is no file to snapshot."""
    src = _db_path(engine)
    if src is None or not src.exists():
        return None
    snap_dir = src.parent / "snapshots"
    snap_dir.mkdir(exist_ok=True)
    # Microsecond resolution keeps the name unique even for back-to-back calls.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = snap_dir / f"{src.stem}-{stamp}.db"
    shutil.copy2(src, dest)
    _prune(snap_dir, src.stem, keep)
    return dest


def _prune(snap_dir: Path, stem: str, keep: int) -> None:
    """Keep only the newest ``keep`` ``<stem>-*.db`` snapshots; delete the rest.

    Sort by the timestamp embedded in the filename (which sorts chronologically),
    not by mtime — ``copy2`` copies the source's mtime, so every snapshot of an
    unchanged DB would share one, making mtime ordering meaningless."""
    snaps = sorted(snap_dir.glob(f"{stem}-*.db"), reverse=True)
    for old in snaps[keep:]:
        try:
            old.unlink()
        except OSError:
            log.warning("Could not delete old snapshot %s", old)


def run_startup_safety(engine, *, keep: int = DEFAULT_KEEP) -> DbSafetyResult:
    """Checkpoint → snapshot → integrity check. Cache and return the result.

    Never raises: a failure to run the checks is captured in ``error`` and logged
    so startup proceeds, but an actual integrity failure sets ``ok=False`` so the
    UI can refuse to continue quietly.
    """
    global LAST_RESULT
    if _db_path(engine) is None:
        LAST_RESULT = DbSafetyResult(skipped=True)
        return LAST_RESULT

    # Checkpoint and snapshot are best-effort and must not abort the run: if the
    # file is so damaged that the checkpoint raises, the integrity check below
    # still runs and reports it (integrity_check swallows its own errors into a
    # problem). The snapshot of even a damaged file is harmless — earlier good
    # snapshots are retained for recovery.
    try:
        checkpoint(engine)
    except Exception:
        log.warning("WAL checkpoint failed at startup (file may be damaged)")
    snap = None
    snap_err = None
    try:
        snap = snapshot(engine, keep=keep)
    except Exception as exc:
        log.exception("Startup snapshot failed")
        snap_err = str(exc)

    problems = integrity_check(engine)

    if problems:
        log.error("DB integrity check FAILED: %s", "; ".join(problems))
        LAST_RESULT = DbSafetyResult(
            ok=False, integrity_problems=problems, snapshot_path=snap, error=snap_err)
    else:
        log.info("DB integrity OK — snapshot %s", snap.name if snap else "(none)")
        LAST_RESULT = DbSafetyResult(ok=True, snapshot_path=snap, error=snap_err)
    return LAST_RESULT
