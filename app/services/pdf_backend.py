"""PDF rendering backends for the label sheet.

The labels are composed as HTML/CSS (see ``labels.py``) and turned into a PDF.
Two backends can do that:

* **WeasyPrint** (``weasyprint``) — the original. Pure-Python API, but binds to
  the native GTK/Pango/Cairo stack, which is the hard part of a Windows install.
* **Chromium** (``chromium``) — headless Chromium via Playwright renders the *same*
  HTML/CSS with HarfBuzz shaping and automatic per-glyph font fallback (so a label
  in any script — Cyrillic, Greek, CJK, Arabic … — renders correctly), and needs
  **no** system libraries: Playwright ships a self-contained browser that behaves
  identically on Windows/macOS/Linux.

Both take the identical HTML string, so the label layout is authored once.

Chromium note: Playwright's *sync* API refuses to run inside a live asyncio loop
(NiceGUI's). We therefore always render on a worker thread, which has no running
loop of its own — safe whether called from the async UI or a plain script.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

# How many times to (re)try the one-time Chromium download before giving up for
# this launch. The download is ~170 MB from cdn.playwright.dev and a flaky
# connection drops it mid-stream (ECONNRESET) or the DNS lookup fails
# (ENOTFOUND) — both recover on a retry, and a partial download resumes rather
# than restarting. A failure here only warns; the next launch tries again.
_INSTALL_ATTEMPTS = 4
_RETRY_BACKOFF_S = (3, 10, 30)  # waited before attempts 2, 3, 4


# Chromium rounds box edges and text baselines to whole CSS px (0.75 pt = 0.26 mm) —
# each independently. On labels a few mm tall that is not noise: identical labels at
# different sub-pixel positions printed with 0.18 mm or 0.45 mm above their first line,
# and the 0.4 mm cut lane printed as 0.26 mm. The sheet is therefore laid out this many
# times larger (`body { zoom }`) and printed 1/_PRINT_GRID as large (`page.pdf(scale)`),
# which leaves every physical dimension unchanged but makes one layout px a 1/4 px of
# paper: 0.19 pt = 0.07 mm, below what a label printer resolves.
# Measured 2026-10-07 on one sheet of 60 identical determination labels (before: 11
# printed a px short at the top; after: top clearance within 0.19 pt everywhere, all
# 56 + 85 cut lanes a true 0.4 mm, a 450-label grid still on the same 2 pages).
# Dead ends, do not re-derive:
#   * a higher device scale factor — print ignores it;
#   * redrawing the hairline (outline, inline SVG, scaled pseudo-element) — the line
#     was never what moved;
#   * a non-identity transform to anchor each label / cell / row to a whole px — makes
#     clearances identical but collapses 13 of 56 cut lanes to ZERO (a transformed box
#     is drawn at its enclosing pixel rect); on the table instead, lanes survive but
#     rows 2+ of a multi-row grid stay wrong. (`scale(1)`, `rotate(0)`, `translateZ(0)`
#     and `will-change` do not anchor at all.)
# The finer grid also un-rounds the line height, which exposed that the sheet had no
# doctype: Chromium rendered it in quirks mode, where a table does not inherit
# `line-height`, so labels printed with the font's `normal` line height (a 3-line
# determination label 5.9 mm tall). `labels._grouped_html` now emits `<!doctype html>`.
# Measured 2026-10-16 (same sheets, Chromium vs WeasyPrint): line pitch 1.39-1.46 mm vs
# 1.41 mm, 3-line determination label 4.96-5.03 mm vs 5.18 mm, top clearance spread
# 0.07 mm, all cut lanes 0.4 mm, the 450-label grid still 2 pages. Chromium sets the
# first baseline ~0.2 mm higher than WeasyPrint (font-metric difference; nothing clips).
_PRINT_GRID = 4


def ensure_chromium() -> None:
    """Install Playwright's Chromium browser if it is missing (idempotent).

    The ``playwright`` pip package ships **no** browser binary — it is fetched
    separately (``playwright install chromium``). The Chromium PDF backend needs
    it, so run.py calls this once at startup; environment.yml documents that
    contract. Safe to call on every launch: a no-op when the browser is already
    present, and a network/permission failure only **warns** (label printing
    degrades to an error at print time, but the app still starts and runs)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if Path(p.chromium.executable_path).exists():
                return  # already installed — nothing to do
    except Exception:
        pass  # not installed (or path lookup failed) → fall through and install

    # --no-shell skips the separate chrome-headless-shell binary: we launch the
    # full Chromium build via channel="chromium" (see _chromium_pdf_sync), so the
    # shell is dead weight — and it was the fragile *second* download that failed
    # on a flaky connection even after the full browser downloaded fine.
    cmd = [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"]
    _log.info("Installing Chromium for label PDFs (one-time download)…")
    for attempt in range(1, _INSTALL_ATTEMPTS + 1):
        try:
            subprocess.run(cmd, check=True)
            _log.info("Chromium installed.")
            return
        except Exception as exc:  # offline / no permissions / dropped download
            if attempt < _INSTALL_ATTEMPTS:
                wait = _RETRY_BACKOFF_S[min(attempt - 1, len(_RETRY_BACKOFF_S) - 1)]
                _log.warning(
                    "Chromium download attempt %d/%d failed (%s) — retrying in %ds…",
                    attempt, _INSTALL_ATTEMPTS, exc, wait)
                time.sleep(wait)
            else:
                _log.warning(
                    "Could not install Chromium after %d attempts (label printing "
                    "will be unavailable until 'python -m playwright install "
                    "--no-shell chromium' succeeds): %s", _INSTALL_ATTEMPTS, exc)


def render_pdf(html: str, backend: str = "weasyprint") -> bytes:
    """Render *html* to PDF bytes with the chosen backend."""
    if backend == "weasyprint":
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    if backend == "chromium":
        return _chromium_pdf(html)
    raise ValueError(f"unknown PDF backend: {backend!r}")


def _chromium_pdf(html: str) -> bytes:
    """Render on a worker thread so Playwright's sync API never sees a running loop."""
    box: dict[str, object] = {}

    def _work() -> None:
        try:
            box["pdf"] = _chromium_pdf_sync(html)
        except BaseException as exc:  # re-raise on the caller's thread
            box["err"] = exc

    t = threading.Thread(target=_work, name="chromium-pdf")
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]  # type: ignore[misc]
    return box["pdf"]  # type: ignore[return-value]


def _chromium_pdf_sync(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    # Write to a real file and navigate to it: a file:// page may load the label's
    # file:// @font-face resources, which a set_content() about:blank page cannot.
    with tempfile.TemporaryDirectory() as td:
        page_file = Path(td) / "sheet.html"
        page_file.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            # channel="chromium" launches the full Chromium build in new-headless
            # mode. Without it, recent Playwright defaults headless launches to the
            # *separate* chrome-headless-shell binary — a second download that may be
            # missing (flaky network) even when the full browser installed fine, so
            # the launch fails with "Executable doesn't exist …chrome-headless-shell".
            browser = p.chromium.launch(
                channel="chromium", args=["--allow-file-access-from-files"])
            try:
                page = browser.new_page()
                page.goto(page_file.as_uri(), wait_until="networkidle")
                page.emulate_media(media="print")
                # Lay the sheet out _PRINT_GRID x larger and print it that much
                # smaller — see _PRINT_GRID. `body`, not `html`: zooming the root
                # also multiplies the @page margin (measured: 5 mm became 20 mm).
                page.add_style_tag(content=f"body {{ zoom: {_PRINT_GRID}; }}")
                # prefer_css_page_size honours the sheet's @page { size: A4 };
                # margins are supplied by the CSS @page, so zero them here.
                return page.pdf(
                    prefer_css_page_size=True,
                    print_background=True,
                    scale=1 / _PRINT_GRID,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
