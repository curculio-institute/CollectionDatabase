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

import tempfile
import threading
from pathlib import Path


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
            browser = p.chromium.launch(args=["--allow-file-access-from-files"])
            try:
                page = browser.new_page()
                page.goto(page_file.as_uri(), wait_until="networkidle")
                page.emulate_media(media="print")
                # prefer_css_page_size honours the sheet's @page { size: A4 };
                # margins are supplied by the CSS @page, so zero them here.
                return page.pdf(
                    prefer_css_page_size=True,
                    print_background=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
