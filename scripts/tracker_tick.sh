#!/usr/bin/env bash
# One scheduler pass of the stock tracker (called by the LaunchAgent every few minutes).
# Keeps data/tracker/logs/tick.log under ~2 MB.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${TRACKER_PYTHON:-/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python}"
LOG_DIR="$ROOT/data/tracker/logs"
LOG="$LOG_DIR/tick.log"
mkdir -p "$LOG_DIR"
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt 2000000 ]; then
  mv -f "$LOG" "$LOG.1"
fi
cd "$ROOT" || exit 1
{
  echo "── $(date '+%Y-%m-%d %H:%M:%S') ──"
  "$PY" -m tracker tick --source launchd
} >> "$LOG" 2>&1
