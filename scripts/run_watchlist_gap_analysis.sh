#!/bin/bash
# Watchlist Gap Analysis
# Cross-references your portfolio's sector coverage (from sector_analysis cache) against
# all companies in watchlist.yaml to find sectors that are absent or thin in your portfolio.
# Produces the top 3 watchlist picks that would most meaningfully diversify your holdings.
# Prerequisite: run sector_analysis first so profiles are cached.
# Output: portfolio/ZV3899/watchlist_gaps/{today}/watchlist_gap_analysis.md

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.watchlist_gap_analysis "$@"
