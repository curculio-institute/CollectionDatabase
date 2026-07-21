"""Best-effort native desktop notification — no dependency, never raises.

Used by auto_shutdown so the user gets visible confirmation the server actually
stopped: the whole point of close-to-quit is that nothing lingers invisibly, so a
notification closes the loop. Uses OS-native tools — ``notify-send`` (Linux),
``osascript`` (macOS), a PowerShell balloon (Windows) — each spawned as a
**separate, detached process that outlives us**, so the notification still appears
while we shut down.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_ICON = Path(__file__).resolve().parents[2] / "app" / "static" / "collection_icon.png"
_CREATE_NO_WINDOW = 0x08000000   # Windows: no console flash for the helper


def notify(title: str, message: str) -> None:
    """Show a native desktop notification. Best-effort; swallows all errors."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["notify-send", "-a", "Collection", "-i", str(_ICON), title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            script = f"display notification {_osa(message)} with title {_osa(title)}"
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            _windows_balloon(title, message)
    except Exception as exc:                      # notifications must never break shutdown
        _log.debug("notify failed (%s)", exc)


def _osa(text: str) -> str:
    """Quote a string as an AppleScript literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _windows_balloon(title: str, message: str) -> None:
    def q(text: str) -> str:                       # PowerShell single-quoted literal
        return "'" + text.replace("'", "''") + "'"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
        "$n.Visible=$true;"
        f"$n.ShowBalloonTip(4000,{q(title)},{q(message)},'Info');"
        "Start-Sleep -Seconds 4;$n.Dispose()"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        creationflags=_CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
