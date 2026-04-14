#!/bin/bash
# Daily Portfolio Health Report
# Generates a 10-section morning dashboard: P&L overview, DMA status, position sizing alerts,
# sector concentration, tax-loss harvest candidates, SIP signals, and watchlist overlap.
# Run every market day before trading. Output: portfolio/ZV3899/{today}/daily_report.md
#
# Usage: bash scripts/run_daily_report.sh [--no-dma]

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report "$@"
