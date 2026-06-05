#!/usr/bin/env bash
# Launch Collection in the background and open the browser.
# Use this for the desktop file / app-menu shortcut.
# For terminal/debug use, run start.sh instead (logs to stdout).

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

# Wait up to 6 s for the server to accept connections, then open the browser.
for i in $(seq 1 20); do
    sleep 0.3
    if curl -sf http://127.0.0.1:8080 > /dev/null 2>&1; then
        break
    fi
done
xdg-open "http://127.0.0.1:8080" 2>/dev/null || true
