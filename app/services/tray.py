"""System-tray front-end launcher — a no-console way to run the Collection app.

This is the end-user front door. On Windows, double-clicking ``Collection.vbs``
starts it with ``pythonw`` (no console window at all); on Linux the ``.desktop``
shortcut / ``collection-tray.sh`` runs it. It puts a tray icon in the system tray
with **Open Collection** and **Quit**, so there is no black console window to look
at, mind, or accidentally close.

**Design — a subprocess supervisor, not an in-process server.** The server is run
exactly as always, ``python run.py --no-browser`` as a child process with no
console, and this launcher only supervises it:

- Sharing nothing with the server's own start-up means a bug in the tray can never
  corrupt a save; the server path is byte-for-byte what the debug launchers run.
- ``--no-browser`` because **this** process opens the UI, through
  ``app.services.launcher`` — the single owner of the tab-vs-app-window choice
  (``config.launch_mode``). So the initial open and the *Open Collection* menu item
  go through one code path, and app mode never gets a stray extra tab.
- If a server is already serving the port, we **attach** (just open the UI) rather
  than starting a duplicate — clicking the launcher twice simply reopens the app.

**Loud on failure (never a silent no-op).** A hidden launcher that fails shows the
user nothing, which is worse than an ugly console. So if the server child dies
before it starts serving, we pop a real error dialog with the tail of the log and
open the full log file — the console's old job of surfacing errors, kept.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

_log = logging.getLogger(__name__)

# Must match run.py's ui.run(host=…, port=…). Localhost only.
HOST = "127.0.0.1"
PORT = 8080
URL = f"http://{HOST}:{PORT}"

# app/services/tray.py → repo root is two parents up.
_REPO = Path(__file__).resolve().parents[2]
_LOG_DIR = _REPO / "data" / "logs"
_SERVER_LOG = _LOG_DIR / "server.log"

# First-ever launch may fetch Playwright's Chromium before the server binds the
# port, so allow a generous cap; a child that *exits* is a failure detected at once.
_STARTUP_TIMEOUT = 180.0
_CREATE_NO_WINDOW = 0x08000000   # Windows: child gets no console window


def _port_open(timeout: float = 0.5) -> bool:
    """True if something is accepting connections on the app's port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((HOST, PORT)) == 0


def _tail(path: Path, n: int = 25) -> str:
    try:
        return "\n".join(
            path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        ) or "(log is empty)"
    except Exception:
        return "(log unavailable)"


def _open_path(path: str) -> None:
    """Open a file or URL with the OS default handler. Never raises."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        _log.warning("Could not open %s (%s)", path, exc)


def _error_dialog(title: str, message: str) -> None:
    """Best-effort blocking error dialog — the tray's 'loud on failure'. Never raises."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        _log.error("%s: %s", title, message)  # no GUI toolkit — log is the fallback


def _open_ui() -> None:
    """Open the UI per config.launch_mode (tab or app window). Never raises."""
    try:
        from app.config import get_config
        from app.services.launcher import open_ui
        open_ui(URL, get_config().launch_mode)
    except Exception as exc:
        _log.warning("Could not open the UI (%s) — open %s manually.", exc, URL)


def _spawn_server() -> subprocess.Popen:
    """Start ``run.py --no-browser`` as a no-console child, logging to a file."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(_SERVER_LOG, "w", encoding="utf-8")
    kwargs: dict = dict(cwd=str(_REPO), stdout=logf, stderr=subprocess.STDOUT)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.Popen(
        [sys.executable, str(_REPO / "run.py"), "--no-browser"], **kwargs)


def _wait_until_up(proc: subprocess.Popen) -> bool:
    """Poll until the server serves, the child dies, or the cap is hit.

    Returns True once the port answers. False means the child exited before
    serving (a startup failure the caller surfaces) — a slow-but-alive child keeps
    waiting until the cap, then also returns False so the caller can proceed with a
    'still starting' note rather than block forever.
    """
    deadline = time.time() + _STARTUP_TIMEOUT
    while time.time() < deadline:
        if _port_open():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def _stop_server(proc: subprocess.Popen) -> None:
    """Terminate the supervised server, escalating to kill if it lingers."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _run_tray(proc: subprocess.Popen | None) -> None:
    """Show the tray icon and block on its event loop until Quit."""
    from PIL import Image
    from pystray import Icon, Menu, MenuItem

    image = Image.open(_REPO / "app" / "static" / "collection_icon.png")

    def _quit(icon, _item) -> None:
        if proc is not None:
            _stop_server(proc)      # only stop a server WE started
        icon.stop()

    menu = Menu(
        MenuItem("Open Collection", lambda i, it: _open_ui(), default=True),
        MenuItem("Open log", lambda i, it: _open_path(str(_SERVER_LOG))),
        MenuItem("Quit", _quit),
    )
    icon = Icon("Collection", image, "Collection", menu)

    def _setup(ic) -> None:
        ic.visible = True
        try:
            ic.notify("Collection is running.", "Collection")
        except Exception:
            pass                    # notifications are backend-dependent; ignore

    icon.run(setup=_setup)


def run() -> None:
    """Entry point: start (or attach to) the server, open the UI, show the tray."""
    logging.basicConfig(level=logging.INFO)

    proc: subprocess.Popen | None = None
    if _port_open():
        _log.info("A server is already running on %s — attaching.", URL)
    else:
        proc = _spawn_server()
        if not _wait_until_up(proc):
            if proc.poll() is not None:          # child exited → hard failure
                _error_dialog(
                    "Collection failed to start",
                    "The app could not start.\n\n"
                    f"Last lines of the log:\n\n{_tail(_SERVER_LOG)}\n\n"
                    f"Full log:\n{_SERVER_LOG}",
                )
                _open_path(str(_SERVER_LOG))
                return
            # Still alive but slow (e.g. first-run Chromium download): fall through,
            # show the tray, and let the user Open once it finishes.
            _log.warning("Server slow to start; showing tray anyway. Log: %s", _SERVER_LOG)

    if _port_open():
        _open_ui()
    _run_tray(proc)


if __name__ == "__main__":
    run()
