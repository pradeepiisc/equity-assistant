#!/bin/bash
# Data Fetch — Raw data for one company (4 steps)
# Step 1: Downloads concall PDFs from Screener.in
# Step 2: Fetches recent news headlines via Google News RSS
# Step 3: Scrapes shareholding pattern + individual holders from Screener.in
# Step 4: Scrapes financial tables (P&L, Balance Sheet, Cash Flow, Ratios) from Screener.in
# Must be run before full_company_analysis. Output: data/companies/{SYMBOL}/
#
# Usage: bash scripts/run_data_fetch.sh SYMBOL [--skip-financials] [--refresh-financials]

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
    echo "Usage: bash scripts/run_data_fetch.sh SYMBOL [options]"
    echo "Example: bash scripts/run_data_fetch.sh ZENTEC"
    exit 1
fi

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.data_fetch "$@"
