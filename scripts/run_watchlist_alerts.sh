#!/bin/bash
# Watchlist Entry Alerts
# For every stock in watchlist.yaml, fetches current price and 50/200 DMA,
# then categorises each as: Fresh Entry / Accumulate / Watch / Extended / Already Held.
# Run every morning alongside the daily report, especially on market dip days.
# Output: portfolio/ZV3899/{today}/watchlist_alerts.md

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.watchlist_alerts "$@"
