#!/usr/bin/env bash
# Collection Database — Linux system-tray launcher (no terminal).
# Runs the tray front end (Open Collection / Quit) via the collection env's
# python. Meant for the desktop / app-menu shortcut (Collection.desktop).
# For a terminal/verbose run with logs on stdout, use start.sh instead.
#
# Adjust PYTHON below if your conda is not miniforge3 in $HOME.
set -euo pipefail

PROJ="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PYTHON="$HOME/miniforge3/envs/collection/bin/python"

cd "$PROJ"
exec "$PYTHON" collection_tray.py
