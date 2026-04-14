#!/bin/bash
# Full Company Analysis (Deep-dive — requires data_fetch first)
# Runs 5 LLM skills in sequence: transcript_analysis, shareholding_analysis,
# news_sentiment, financial_snapshot, peer_comparison — then synthesises a
# master report with investment rating, catalysts, risks, and monitoring triggers.
# Output: data/companies/{SYMBOL}/reports/master_report.md
#
# Usage: bash scripts/run_full_analysis.sh SYMBOL

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
    echo "Usage: bash scripts/run_full_analysis.sh SYMBOL"
    echo "Example: bash scripts/run_full_analysis.sh ZENTEC"
    exit 1
fi

/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.full_company_analysis "$@"
