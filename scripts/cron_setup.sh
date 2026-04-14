#!/bin/bash
# Cron Job Setup for Equity Assistant
# ====================================
# Installs two cron jobs:
#
# 1. DAILY RUN — Mon-Fri at 5:30pm IST
#    Generates portfolio report + watchlist alerts + news digest.
#    Prerequisites: Kite login + save-portfolio must be done manually earlier.
#
# 2. EARNINGS SEASON — Mon-Sat at 8pm IST during results months
#    Months: Jan, Feb, Apr, May, Jul, Aug, Oct, Nov
#    (Gap months: Mar, Jun, Sep, Dec — no runs)
#    Scans for new transcripts/results/shareholding, DMA deployment analysis.
#
# Usage:
#   bash scripts/cron_setup.sh              # install both cron jobs
#   bash scripts/cron_setup.sh --remove     # remove both cron jobs
#   bash scripts/cron_setup.sh --show       # show current cron entries
#
# Logs are saved to: logs/daily_cron_YYYY-MM-DD.log
#                     logs/earnings_season_YYYY-MM-DD.log

set -e

PROJECT_DIR="/Users/pradeep.bansal1/Documents/learning/equity-assistant"
DAILY_SCRIPT="$PROJECT_DIR/scripts/run_daily_cron.sh"
SEASON_SCRIPT="$PROJECT_DIR/scripts/run_earnings_season.sh"
LOG_DIR="$PROJECT_DIR/logs"

# Cron marker comments (used for identification)
DAILY_MARKER="# equity-assistant-daily"
SEASON_MARKER="# equity-assistant-earnings-season"

# Cron schedule
# Daily: 5:30pm IST, Mon-Fri
DAILY_CRON="30 17 * * 1-5"
# Earnings season: 8pm IST, Mon-Sat, results months only
SEASON_CRON="0 20 * 1,2,4,5,7,8,10,11 1-6"

show_cron() {
    echo "Current equity-assistant cron entries:"
    echo "───────────────────────────────────────"
    crontab -l 2>/dev/null | grep -A1 "equity-assistant" || echo "  (none found)"
    echo ""
}

remove_cron() {
    echo "Removing equity-assistant cron entries..."
    crontab -l 2>/dev/null | grep -v "equity-assistant" | crontab - 2>/dev/null || true
    echo "✓ Removed"
}

install_cron() {
    # Create log dir
    mkdir -p "$LOG_DIR"

    # Make scripts executable
    chmod +x "$DAILY_SCRIPT" "$SEASON_SCRIPT"

    # Remove old entries first
    local existing
    existing=$(crontab -l 2>/dev/null | grep -v "equity-assistant" || true)

    # Build new crontab
    local new_crontab="$existing"

    # Daily run
    new_crontab="$new_crontab
$DAILY_MARKER
$DAILY_CRON bash $DAILY_SCRIPT >> $LOG_DIR/daily_cron_\$(date +\\%Y-\\%m-\\%d).log 2>&1"

    # Earnings season (with --fetch to also grab new transcripts)
    new_crontab="$new_crontab
$SEASON_MARKER
$SEASON_CRON bash $SEASON_SCRIPT --fetch >> $LOG_DIR/earnings_season_\$(date +\\%Y-\\%m-\\%d).log 2>&1"

    echo "$new_crontab" | crontab -

    echo "✓ Cron jobs installed:"
    echo ""
    echo "  📅 DAILY (Mon-Fri 5:30pm):"
    echo "     $DAILY_CRON bash $DAILY_SCRIPT"
    echo ""
    echo "  📊 EARNINGS SEASON (Mon-Sat 8pm, Jan-Feb/Apr-May/Jul-Aug/Oct-Nov):"
    echo "     $SEASON_CRON bash $SEASON_SCRIPT --fetch"
    echo ""
    echo "  Logs → $LOG_DIR/"
    echo ""
    echo "  ⚠️  Daily cron needs Kite login done manually before 5:30pm."
    echo "     Run: python -m integrations.kite_connect --login --save-portfolio"
    echo ""
}

case "${1:-}" in
    --remove)
        remove_cron
        ;;
    --show)
        show_cron
        ;;
    *)
        install_cron
        ;;
esac
