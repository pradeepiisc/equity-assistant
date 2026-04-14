#!/bin/bash
# Batch Company Analysis — All watchlist companies
# Runs data_fetch + full_company_analysis for every company in watchlist.yaml.
# Use --skip-existing to skip companies that already have a master_report.md.
# Typical runtime: ~5-10 min per company (LLM calls). Full batch ~2-4 hours.
# Output: data/companies/{SYMBOL}/reports/master_report.md for each company
#
# Usage: bash scripts/run_batch_analysis.sh [--skip-existing] [--data-only] [--analysis-only] [--symbols SYM1 SYM2]

set -e
cd "$(dirname "$0")/.."

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.batch_company_analysis "$@"
