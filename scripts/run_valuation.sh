#!/bin/bash
# Valuation Agent — Sector-aware forward-looking fair value estimation
#
# Detects company sector from sector_profiles cache → loads expert prompt →
# calls LLM to estimate intrinsic fair value using financials + concall
# guidance + shareholding trend. Produces Bull/Base/Bear scenarios,
# entry/exit triggers, and fair value range (₹low–₹high).
#
# Output: data/companies/{SYMBOL}/reports/valuation_agent.md
#         data/companies/{SYMBOL}/reports/valuation_agent.json
#
# Usage:
#   bash scripts/run_valuation.sh SYMBOL
#   bash scripts/run_valuation.sh TARIL
#   bash scripts/run_valuation.sh YASHO
#
# Note: Company must be in watchlist.yaml for best results.
#       Run data_fetch first to populate financials + transcripts.

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
    echo "Usage: bash scripts/run_valuation.sh SYMBOL"
    echo "Example: bash scripts/run_valuation.sh TARIL"
    echo ""
    echo "Tip: run data_fetch first for best results:"
    echo "  bash scripts/run_data_fetch.sh SYMBOL"
    exit 1
fi

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m skills.valuation_agent "$@"
