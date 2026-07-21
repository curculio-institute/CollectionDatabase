"""Convenience launcher: python run.py [--no-browser]

--no-browser suppresses auto-opening the UI (for headless/debug runs, e.g.
start.sh or a Playwright-driven test on a spare port). Without it the app opens
per config.launch_mode: a normal browser tab, or a chromeless app window.
"""
import argparse
import logging
from pathlib import Path
# Alias NiceGUI's `app`: a later `import app.ui.main` binds the name `app` to our
# own top-level package, which would shadow this and break `app.on_startup` below.
from nicegui import ui, app as ng_app

logging.basicConfig(level=logging.INFO)

_args = argparse.ArgumentParser(description="Collection Database launcher")
_args.add_argument("--no-browser", action="store_true",
                   help="do not auto-open the UI in a browser")
_cli = _args.parse_args()

# WeasyPrint is used only as a text-width ruler in labels._fits_one_line (the PDF
# itself is rendered by the Chromium backend). Its stylesheet carries
# `text-rendering: geometricPrecision` for Chromium, which WeasyPrint doesn't know
# and warns about on every measurement — harmless noise, so quiet it to errors.
logging.getLogger("weasyprint").setLevel(logging.ERROR)

ng_app.add_static_files('/static', Path(__file__).parent / 'app' / 'static')

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

# Self-register a Linux application-menu entry with correct absolute paths (no-op
# off Linux, idempotent, never raises). So the app appears in the menu / launcher
# after the first start, pointing at the no-console tray front end.
import app.services.desktop_entry as _desktop_entry

_desktop_written = _desktop_entry.ensure_desktop_entry()
if _desktop_written:
    logging.getLogger(__name__).info("Desktop entry: wrote %s", _desktop_written)

# The Chromium label-PDF backend needs Playwright's browser binary, which the pip
# package does not ship. Fetch it once on first launch (idempotent no-op after).
import app.services.pdf_backend as _pdf_backend

_pdf_backend.ensure_chromium()

import app.ui.main  # registers the @ui.page('/') route  # noqa: F401

# Open the UI once the server is up. NiceGUI's own show= uses the same on_startup
# hook, so it fires late enough that the page is being served. We do it here (not
# in the shell launchers) because the tab-vs-app choice lives in config.json, which
# only Python reads — one cross-platform owner. --no-browser opts a debug/headless
# run out. show=False so NiceGUI doesn't also open a second tab.
_APP_URL = "http://127.0.0.1:8080"
if not _cli.no_browser:
    import app.services.launcher as _launcher
    from app.config import get_config as _get_config

    ng_app.on_startup(lambda: _launcher.open_ui(_APP_URL, _get_config().launch_mode))

ui.run(
    host="127.0.0.1",   # localhost only — not exposed on the network
    title="Collection",
    port=8080,
    reload=False,
    show=False,
    favicon=Path(__file__).parent / "app" / "static" / "collection_icon.png",
)
