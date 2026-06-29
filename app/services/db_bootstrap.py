"""First-run / upgrade bootstrap: bring the database schema up to head.

``data/`` is gitignored (CLAUDE.md §1 — the DB is never committed), so a fresh
checkout from GitHub has **no** ``collection.db`` at all. ``get_engine()`` only
opens a connection; it never creates the schema. Without this step the first
SQLite connect would create an empty, table-less file and every query would
crash.

Running ``alembic upgrade head`` at startup fixes both first-run cases with one
idempotent call:

* **fresh clone** — empty/absent DB → all migrations applied, schema built;
* **existing install after ``git pull``** — only the newly added migrations run.

When the DB is already current this is a no-op. It is invoked from ``run.py``
*after* the data-safety snapshot, so the pre-migration state is always captured
before any DDL runs (the snapshot-before-migrate rule, CLAUDE.md §8).
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


def upgrade_to_head() -> None:
    """Apply any pending Alembic migrations so the schema is at head.

    Paths are forced absolute so this works no matter the current working
    directory: ``script_location`` here, and the DB URL inside ``alembic/env.py``
    (which already rewrites the relative ``data/collection.db`` to an absolute
    path).
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    log.info("Ensuring database schema is at head (alembic upgrade head)…")
    command.upgrade(cfg, "head")
