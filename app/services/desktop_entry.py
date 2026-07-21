"""Self-register a Linux application-menu entry, with correct paths, at startup.

A shipped ``Collection.desktop`` can only carry placeholder paths the user must
hand-edit (which interpreter? where is the repo?). Generating it at startup avoids
that entirely: we already know the real interpreter (``sys.executable`` — the env
python actually running us, so no conda-activation and no hard-coded miniforge
path), the real repo location (from ``__file__``), and the real icon path. The
entry launches the app with no terminal (``run.py --auto-shutdown``), so closing
the window quits the server rather than leaving it running invisibly.

Written to ``~/.local/share/applications/`` (the per-user location the app menu
scans). Idempotent and self-healing: rewritten only when the desired content
differs from what is on disk, so moving the repo fixes the menu entry on the next
launch. Linux only (Windows/macOS use their own mechanisms) and never raises —
desktop integration must never take down startup.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_ENTRY_NAME = "collection-database.desktop"


def _refresh_menu_cache(applications_dir: Path) -> None:
    """Tell the desktop environment to re-read the menu database. Best-effort.

    KDE Plasma caches menu entries (sycoca) and does NOT reliably notice a rewritten
    ``Exec``, so a changed entry keeps launching the OLD command until the cache is
    rebuilt — e.g. an entry that once pointed at a since-deleted script would fail
    silently forever. Rebuild after every write: ``update-desktop-database`` (the
    freedesktop standard, covers GNOME/XFCE/…) plus ``kbuildsycoca`` (KDE). Never
    raises; missing tools are simply skipped.
    """
    cmds: list[list[str]] = []
    if shutil.which("update-desktop-database"):
        cmds.append(["update-desktop-database", str(applications_dir)])
    kbuildsycoca = shutil.which("kbuildsycoca6") or shutil.which("kbuildsycoca5")
    if kbuildsycoca:
        cmds.append([kbuildsycoca])
    for cmd in cmds:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            _log.debug("menu-cache refresh %s failed (%s)", cmd[0], exc)


def _quote(path: str) -> str:
    """Quote a path for a .desktop Exec value (spec: double-quote, escape \\ and \")."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render(repo: Path) -> str:
    python = _quote(sys.executable)
    entry = _quote(str(repo / "run.py"))
    icon = repo / "app" / "static" / "collection_icon.png"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Collection Database\n"
        "Comment=Entomological specimen collection manager\n"
        f"Exec={python} {entry} --auto-shutdown\n"
        f"Path={repo}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Science;Database;\n"
    )


def ensure_desktop_entry() -> str | None:
    """Create/refresh the app-menu entry. Returns the path if written, else None."""
    if not sys.platform.startswith("linux"):
        return None
    repo = Path(__file__).resolve().parents[2]
    target = Path.home() / ".local" / "share" / "applications" / _ENTRY_NAME
    content = _render(repo)
    try:
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return None                      # already current — nothing to do
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _refresh_menu_cache(target.parent)   # so the DE picks up the new Exec now
        return str(target)
    except OSError as exc:
        _log.warning("Could not write desktop entry %s (%s)", target, exc)
        return None
