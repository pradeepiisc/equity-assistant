#!/bin/bash
# Investor Activity Tracker (Weekly)
# Scrapes Screener.in shareholding tables for high-conviction holdings + watchlist stocks.
# Shows Promoter/FII/DII/Public stake across last 4 quarters, flags changes >=1.5pp QoQ.
# Run weekly or immediately after quarterly results season.
# Output: portfolio/ZV3899/{today}/investor_activity.md
#
# Usage: bash scripts/run_investor_activity.sh [--min-alloc 1.0] [--symbols SYM1 SYM2]

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.investor_activity "$@"
