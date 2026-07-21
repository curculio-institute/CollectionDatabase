"""Entry point for the system-tray launcher (the no-console end-user front door).

Run with ``pythonw collection_tray.py`` on Windows (see ``Collection.vbs`` /
``Start-Collection-Tray.bat``) or the collection env's python on Linux/macOS
(see ``collection-tray.sh`` / ``Collection.desktop``). Being at the repo root, it
puts the repo on ``sys.path`` so ``app.*`` imports resolve. The real logic lives
in ``app/services/tray.py``. For a terminal/verbose run use ``start.sh`` instead.
"""
from app.services.tray import run

if __name__ == "__main__":
    run()
