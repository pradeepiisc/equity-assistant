#!/bin/bash
# Daily Portfolio Cron Script
# Runs the report-generation steps of the daily run WITHOUT Kite login.
# Assumes you've already logged in + saved portfolio snapshot earlier in the day.
#
# What it runs (in order):
#   1. Daily portfolio health report (DMA, position sizing, movers)
#   2. Watchlist entry alerts (DMA-based signals)
#   3. News digest (Google News RSS headlines)
#
# What it does NOT do (requires manual Kite login):
#   - Kite OAuth login
#   - Save portfolio snapshot
#
# Usage:
#   bash scripts/run_daily_cron.sh                  # auto-detect user
#   bash scripts/run_daily_cron.sh --user ZV3899    # specific user
#
# For crontab: see scripts/cron_setup.sh

set -e
cd "$(dirname "$0")/.."

PYTHON="/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python"
LOG_DIR="$(pwd)/logs"
mkdir -p "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
LOG="$LOG_DIR/daily_cron_${DATE}.log"

echo "═══════════════════════════════════════════" >> "$LOG" 2>&1
echo "  Daily Cron — $(date)" >> "$LOG" 2>&1
echo "═══════════════════════════════════════════" >> "$LOG" 2>&1

echo "[1/3] Portfolio daily report..." >> "$LOG" 2>&1
$PYTHON -m workflows.portfolio_daily_report "$@" >> "$LOG" 2>&1 || echo "  [!] Report failed" >> "$LOG" 2>&1

echo "[2/3] Watchlist alerts..." >> "$LOG" 2>&1
$PYTHON -m workflows.watchlist_alerts "$@" >> "$LOG" 2>&1 || echo "  [!] Alerts failed" >> "$LOG" 2>&1

echo "[3/3] News digest..." >> "$LOG" 2>&1
$PYTHON -m workflows.news_digest "$@" >> "$LOG" 2>&1 || echo "  [!] News failed" >> "$LOG" 2>&1

echo "" >> "$LOG" 2>&1
echo "✓ Daily cron complete — $(date)" >> "$LOG" 2>&1
echo "  Log: $LOG"
