"""Open the Collection UI in the user's browser — a normal tab or a chromeless
``--app`` window — according to ``config.launch_mode``.

App mode deliberately uses a **real Chromium-class browser** (Edge/Chrome/Chromium)
in ``--app`` window mode, not a pywebview native window: the window is chromeless
and looks like a desktop app, but it is still a full browser, so the beforeunload
unsaved-changes guard (#41), inline PDF viewing, and ``/media`` tabs all keep
working. See CLAUDE.md §3 "The browser is the UI" for why native windows are
rejected. Every genuinely external link in the app is authored ``target="_blank"``
so it opens *outside* the chromeless window rather than navigating it away.

Nothing here raises: a launcher failure must never take down the server. App mode
falls back to a normal browser tab when no Chromium-class browser is found.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

_log = logging.getLogger(__name__)


def _chromium_browser() -> list[str] | None:
    """argv prefix for a Chromium-class browser that supports ``--app``, or None.

    Preference order Edge → Chrome → Chromium → Brave. On Windows the default
    profile is shared (no ``--user-data-dir``) so app-window ``_blank`` links open
    in the user's ordinary, already-signed-in browser session.
    """
    if sys.platform.startswith("win"):
        candidates: list[Path] = []
        for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            root = os.environ.get(var)
            if not root:
                continue
            candidates += [
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(root) / "Chromium" / "Application" / "chrome.exe",
                Path(root) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ]
        for exe in candidates:
            if exe.is_file():
                return [str(exe)]
        for name in ("msedge", "chrome", "chromium", "brave"):
            found = shutil.which(name)
            if found:
                return [found]
        return None

    if sys.platform == "darwin":
        apps = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        for exe in apps:
            if Path(exe).is_file():
                return [exe]
        return None

    # Linux / other POSIX
    for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome",
                 "google-chrome-stable", "chromium", "chromium-browser",
                 "brave-browser"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def open_ui(url: str, mode: str = "tab") -> None:
    """Open *url* in the browser per *mode* (``"tab"`` | ``"app"``). Never raises."""
    if mode == "app":
        browser = _chromium_browser()
        if browser is not None:
            try:
                subprocess.Popen(
                    browser + [f"--app={url}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _log.info("Opened app window via %s", Path(browser[0]).name)
                return
            except Exception as exc:  # launch failed → degrade to a normal tab
                _log.warning("App-window launch failed (%s) — opening a browser tab.", exc)
        else:
            _log.info("No Chromium-class browser found for app mode — opening a browser tab.")
    try:
        webbrowser.open(url)
    except Exception as exc:  # headless / no browser at all — just tell the user
        _log.warning("Could not open a browser (%s). Open %s manually.", exc, url)
