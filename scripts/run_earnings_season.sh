#!/bin/bash
# Earnings Season Report
# Delta-aware quarterly workflow: scans for new transcripts/results/shareholding changes,
# classifies DMA40+DMA100 trends, shows sector allocation, deployment candidates, caution list.
#
# Usage:
#   bash scripts/run_earnings_season.sh                     # delta report only (fast, offline)
#   bash scripts/run_earnings_season.sh --fetch             # also fetch new transcripts
#   bash scripts/run_earnings_season.sh --fetch-all         # full fetch (needs Chrome CDP)
#   bash scripts/run_earnings_season.sh --full-refresh      # ignore previous state
#   bash scripts/run_earnings_season.sh --user ZV3899       # specific user
#
# Output: data/earnings_season/{Quarter}/ (shareholding.md, results.md, concalls.md)

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season "$@"
