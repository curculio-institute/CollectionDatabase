#!/usr/bin/env bash
# Launch Collection in the background (detached from the terminal), logging to a
# file. Use this for the desktop file / app-menu shortcut.
# For terminal/debug use, run start.sh instead (logs to stdout).
#
# run.py opens the UI itself once the server is up (a browser tab or a chromeless
# app window, per the Launch mode setting) — so this script does NOT open the
# browser, or app mode would get a stray extra tab.

set -euo pipefail

PROJ="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PYTHON="$HOME/miniforge3/envs/collection/bin/python"
LOG="/tmp/collection_app.log"

# Kill any running instance of this app before starting fresh.
pkill -f "envs/collection/bin/python.*run\.py" 2>/dev/null || true
sleep 0.4

cd "$PROJ"
nohup "$PYTHON" run.py > "$LOG" 2>&1 &
APP_PID=$!
echo "Collection started (PID $APP_PID) — tail -f $LOG"
