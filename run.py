"""Convenience launcher: python run.py"""
import logging
from pathlib import Path
from nicegui import ui, app

logging.basicConfig(level=logging.INFO)

# WeasyPrint is used only as a text-width ruler in labels._fits_one_line (the PDF
# itself is rendered by the Chromium backend). Its stylesheet carries
# `text-rendering: geometricPrecision` for Chromium, which WeasyPrint doesn't know
# and warns about on every measurement — harmless noise, so quiet it to errors.
logging.getLogger("weasyprint").setLevel(logging.ERROR)

app.add_static_files('/static', Path(__file__).parent / 'app' / 'static')

# ── Data-safety checks: checkpoint WAL, snapshot, verify integrity ──────────
# Run before the UI serves any page so a damaged file is caught up front and a
# fresh launch snapshot exists. The result is cached in db_safety.LAST_RESULT;
# the page handler reads it to show a blocking banner on integrity failure.
from app.database import get_engine
import app.services.db_safety as db_safety
import app.services.db_bootstrap as db_bootstrap

# Snapshot + integrity-check the pre-migration state first, then bring the schema
# up to head. On a fresh GitHub checkout there is no DB yet (data/ is gitignored),
# so this is what builds the schema; on an existing install it applies only the
# migrations added since last launch. Idempotent when already current.
db_safety.run_startup_safety(get_engine())
db_bootstrap.upgrade_to_head()

# WCVP is a name source like any other, so it lives under data/name_sources/wcvp. Move an
# older data/wcvp there once, before anything reads the index — a rename, never a re-download
# (the archive is ~88 MB, the index ~270 MB).
from app import config as _config

_moved = _config.migrate_legacy_dirs()
if _moved:
    logging.getLogger(__name__).info("Name sources: %s", _moved)

# The Chromium label-PDF backend needs Playwright's browser binary, which the pip
# package does not ship. Fetch it once on first launch (idempotent no-op after).
import app.services.pdf_backend as _pdf_backend

_pdf_backend.ensure_chromium()

import app.ui.main  # registers the @ui.page('/') route  # noqa: F401

ui.run(
    host="127.0.0.1",   # localhost only — not exposed on the network
    title="Collection",
    port=8080,
    reload=False,
    show=False,
    favicon=Path(__file__).parent / "app" / "static" / "collection_icon.png",
)
