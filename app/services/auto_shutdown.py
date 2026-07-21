"""Shut the server down when the last browser window closes (end-user mode).

The hidden front-door launcher (``run.py --auto-shutdown``) has no terminal and no
tray, so the only 'stop the app' gesture left is closing the window — and a local
single-user app that keeps a server running invisibly after its window is gone is a
trap. So we make closing the last window quit the server, the way a native desktop
app does: in app mode the chromeless window *is* the app.

Gated behind ``--auto-shutdown`` (only the front-door launchers pass it) so a
developer running ``start.sh`` keeps a server that survives reloads and tab-closes.

Two guards, both needed:
- **A grace window** after the last disconnect, so a page *reload* or navigation
  (disconnect immediately followed by a reconnect) does not quit — only a real
  close, with no client back within the window, does.
- **A startup guard**: if no window ever connects (the browser failed to open),
  quit rather than linger forever as the very invisible server this feature exists
  to prevent.

Opening a label PDF or a ``/media`` image is a plain file response, not a NiceGUI
client, so it never counts as a window and never keeps the server alive.
"""
from __future__ import annotations

import asyncio
import logging

from app.services import notify as _notify

_log = logging.getLogger(__name__)

_GRACE_SECONDS = 5.0        # survive a reload before deciding the window is gone
_STARTUP_SECONDS = 60.0     # if no window connects by now, the browser never opened


def register(app, *, grace: float = _GRACE_SECONDS,
             startup_grace: float = _STARTUP_SECONDS) -> None:
    """Wire close-to-quit onto the NiceGUI *app*. Call before ui.run()."""
    clients: set[str] = set()
    ever_connected = False

    @app.on_connect
    def _on_connect(client) -> None:
        nonlocal ever_connected
        ever_connected = True
        clients.add(client.id)

    @app.on_disconnect
    async def _on_disconnect(client) -> None:
        clients.discard(client.id)
        if clients:
            return
        await asyncio.sleep(grace)
        if clients:                     # a window came back within the grace window
            return
        _log.info("Last window closed — shutting down the server.")
        _notify.notify("Collection Database", "The app has been shut down.")
        app.shutdown()

    async def _startup_guard() -> None:
        await asyncio.sleep(startup_grace)
        if not ever_connected and not clients:
            _log.warning("No window connected within %ss — shutting down.", startup_grace)
            _notify.notify("Collection Database",
                           "Could not open a window — the app has been shut down.")
            app.shutdown()

    app.on_startup(_startup_guard)
