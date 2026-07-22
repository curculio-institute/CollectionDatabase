#!/usr/bin/env bash
# ===========================================================================
#  Collection Database — macOS front door (double-click in Finder)
#
#  Double-click to start the app. It:
#    1. finds the 'collection' conda environment's python (no `conda activate`
#       needed — conda is usually a shell function, absent in this context),
#    2. starts the server DETACHED with --auto-shutdown, so closing the app
#       window quits the server (with a desktop notification) and nothing is
#       left running once this launcher and its Terminal window are gone,
#    3. closes its own Terminal window so no console lingers.
#
#  run.py opens the UI itself once the server is up (a browser tab or a
#  chromeless app window, per the Launch mode setting) — we do NOT open the
#  browser here as well, or app mode would get a stray extra tab.
#
#  macOS has no `pythonw`, and a .command always launches Terminal, so a brief
#  Terminal flash on start is unavoidable; the detach + auto-close keeps it from
#  lingering. A zero-flash launch would need an AppleScript .app wrapper.
#
#  FIRST RUN: Gatekeeper may refuse a downloaded .command ("unidentified
#  developer"). Right-click → Open (once), or in Terminal run:
#      chmod +x Collection.command
#      xattr -d com.apple.quarantine Collection.command   # if quarantined
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Show a Finder-native error dialog (never fail silently). Best-effort.
err() {
    /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} \
default button \"OK\" with icon caution with title \"Collection Database\"" \
        >/dev/null 2>&1 || true
}

# --- Locate the 'collection' environment's python --------------------------
PY=""
for root in \
    "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" \
    "$HOME/opt/miniforge3" "$HOME/opt/miniconda3" "$HOME/opt/anaconda3" \
    "/opt/miniforge3" "/opt/miniconda3" "/opt/anaconda3" \
    "/opt/homebrew/Caskroom/miniforge/base"; do
    if [ -x "$root/envs/collection/bin/python" ]; then
        PY="$root/envs/collection/bin/python"
        break
    fi
done

# Fall back to conda's own base if `conda` happens to be on PATH.
if [ -z "$PY" ] && command -v conda >/dev/null 2>&1; then
    base="$(conda info --base 2>/dev/null || true)"
    if [ -n "$base" ] && [ -x "$base/envs/collection/bin/python" ]; then
        PY="$base/envs/collection/bin/python"
    fi
fi

if [ -z "$PY" ]; then
    err "Could not find the 'collection' Anaconda/Miniconda environment.\n\n\
Open Terminal in this folder and run once:\n\
    conda env create -f environment.yml\n\
    conda activate collection"
    exit 1
fi

# --- Start the server, detached, no lingering console ----------------------
# nohup + background + disown detaches it so it survives this launcher and the
# Terminal window closing; --auto-shutdown quits it when the app window closes.
nohup "$PY" run.py --auto-shutdown >/tmp/collection-database.log 2>&1 &
disown 2>/dev/null || true

# Tidy up: close this Terminal window (Terminal-scoped, so it can only ever
# close a Terminal window, never another app). The server already runs detached.
/usr/bin/osascript -e 'tell application "Terminal" to close front window' \
    >/dev/null 2>&1 &
exit 0
