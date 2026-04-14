#!/bin/bash
# Sector Analysis (Weekly)
# Fetches LLM-enriched company profiles from Screener.in for all portfolio holdings,
# groups them by PRIMARY output industry (where products are directly sold), and
# produces a sector map report. Profiles are cached for 30 days.
# Output: portfolio/ZV3899/{snapshot_date}/sector_analysis.md
#
# Usage: bash scripts/run_sector_analysis.sh [--symbols SYM1 SYM2] [--refresh] [--no-llm]

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.sector_analysis "$@"
