#!/usr/bin/env bash
# Launch the Equity Assistant local UI
# Usage: bash scripts/run_ui.sh [--port 7777]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python"
PORT="${1:-7777}"

cd "$ROOT"
echo "Starting Equity Assistant UI on http://localhost:${PORT}"
echo "Press Ctrl+C to stop."
echo ""
"$PYTHON" -m uvicorn ui.server:app --host 127.0.0.1 --port "$PORT" --reload
