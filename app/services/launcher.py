"""Open the Collection UI in the user's browser — a normal tab or a chromeless
``--app`` window — according to ``config.launch_mode``.

App mode deliberately uses a **real Chromium-class browser** (Chrome/Chromium/Edge/
Brave) in ``--app`` window mode, not a pywebview native window: the window is
chromeless and looks like a desktop app, but it is still a full browser, so the
beforeunload unsaved-changes guard (#41), inline PDF viewing, and ``/media`` tabs
all keep working. See CLAUDE.md §3 "The browser is the UI" for why native windows
are rejected. Every genuinely external link in the app is authored
``target="_blank"`` so it opens *outside* the chromeless window rather than
navigating it away.

Firefox cannot provide app mode — the chromeless window is Chromium's ``--app``
feature, which Firefox has no equivalent for — so when no Chromium-class browser is
installed, app mode degrades to a normal tab (opening the user's default browser,
e.g. Firefox). That degrade is **not silent**: the user is told both by a native
system notification fired here and by an in-app notice on the page (via the
``app_mode_fallback`` module flag, read by the ``@ui.page`` handler).

Nothing here raises: a launcher failure must never take down the server.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from app.services import notify as _notify

_log = logging.getLogger(__name__)

# Set by open_ui() when app mode was requested but no Chromium-class browser was
# available (or its launch failed), so the running page can add an in-app notice
# on top of the system notification fired here. Read — and cleared — once by the
# @ui.page handler in app/ui/main.py.
app_mode_fallback: str | None = None


def _chromium_browser() -> list[str] | None:
    """argv prefix for a Chromium-class browser that supports ``--app``, or None.

    Preference order Chrome → Chromium → Edge → Brave. On Windows the default
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
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(root) / "Chromium" / "Application" / "chrome.exe",
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(root) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ]
        for exe in candidates:
            if exe.is_file():
                return [str(exe)]
        for name in ("chrome", "chromium", "msedge", "brave"):
            found = shutil.which(name)
            if found:
                return [found]
        return None

    if sys.platform == "darwin":
        apps = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        for exe in apps:
            if Path(exe).is_file():
                return [exe]
        return None

    # Linux / other POSIX
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "microsoft-edge", "microsoft-edge-stable",
                 "brave-browser"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def chromium_available() -> bool:
    """True if a Chromium-class browser for app mode is installed.

    Settings uses this to warn immediately when 'App window' is chosen on a
    machine that has none, so the user learns at the moment they pick it rather
    than only at the next launch.
    """
    return _chromium_browser() is not None


def open_ui(url: str, mode: str = "tab") -> None:
    """Open *url* in the browser per *mode* (``"tab"`` | ``"app"``). Never raises."""
    global app_mode_fallback
    if mode == "app":
        browser = _chromium_browser()
        if browser is not None:
            try:
                # --start-maximized fills the screen instead of opening a slim
                # default window (verified in --app mode on Linux/KDE). If a future
                # platform is found to ignore it in --app mode, add an explicit
                # --window-size there rather than for everyone.
                # start_new_session detaches the browser from our process group, so
                # it survives run.py exiting immediately after (the attach path) and
                # is not taken down when the server is later restarted/stopped.
                # NB: --class does NOT set the Wayland app_id of an --app window —
                # Chromium derives that from the URL host (measured:
                # "chrome-127.0.0.1__-Default", identical across ports, --class
                # ignored). The taskbar-icon match is handled compositor-side via
                # that app_id in desktop_entry's StartupWMClass, not here.
                subprocess.Popen(
                    browser + [f"--app={url}", "--start-maximized"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                _log.info("Opened app window via %s", Path(browser[0]).name)
                return
            except Exception as exc:  # launch failed → degrade to a normal tab, loudly
                _log.warning("App-window launch failed (%s) — opening a browser tab.", exc)
                _notify.notify(
                    "Collection Database",
                    "The app window failed to open — showing the app in a browser tab instead.")
                app_mode_fallback = (
                    "The app window failed to open, so the app opened in a browser tab instead.")
        else:
            # App mode selected but no Chromium-class browser is installed. Do NOT
            # degrade silently (CLAUDE.md: loud failure > silent surprise) — tell the
            # user via a system notification (reaches them even from the no-terminal
            # launcher) and stash an in-app notice, then open a normal tab.
            _log.info("No Chromium-class browser found for app mode — opening a browser tab.")
            _notify.notify(
                "Collection Database",
                "App window needs Chrome, Chromium, Edge or Brave — none is installed. "
                "Opened in a browser tab instead.")
            app_mode_fallback = (
                "App-window mode needs a Chromium-based browser (Chrome, Chromium, Edge "
                "or Brave); none is installed, so the app opened in a browser tab. Install "
                "one, or switch to Browser-tab mode in Settings to silence this notice.")
    try:
        webbrowser.open(url)
    except Exception as exc:  # headless / no browser at all — just tell the user
        _log.warning("Could not open a browser (%s). Open %s manually.", exc, url)
