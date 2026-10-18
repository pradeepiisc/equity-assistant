#!/usr/bin/env bash
# Start the stock tracker dashboard: http://127.0.0.1:7788
# Usage: bash scripts/run_tracker.sh [port]
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${TRACKER_PYTHON:-/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python}"
cd "$ROOT"
exec "$PY" -m tracker serve ${1:+--port "$1"}
