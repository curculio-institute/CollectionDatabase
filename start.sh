#!/usr/bin/env bash
set -e
pkill -f "envs/collection/bin/python.*run\.py" 2>/dev/null || true
sleep 0.3
cd "$(dirname "$0")"
exec "$HOME/miniforge3/envs/collection/bin/python" run.py
