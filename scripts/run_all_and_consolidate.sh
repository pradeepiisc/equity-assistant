#!/usr/bin/env bash

# Exit Strategy End-to-End Workflow
# 1. Runs full company analysis for all companies with existing data
# 2. Generates the consolidated Exit Strategy Dashboard

set -e

echo "============================================================"
echo "  STARTING END-TO-END EXIT STRATEGY WORKFLOW"
echo "============================================================"
echo "This will process ~160 companies. It may take a few hours."
echo ""

# Step 1: Run the LLM analysis for all companies
# We use --all to pick up everything in data/companies/
# We use --analysis-only to skip scraping/fetching and use existing data files
python -m workflows.batch_company_analysis --all --analysis-only

echo ""
echo "============================================================"
echo "  BATCH ANALYSIS COMPLETE. GENERATING CONSOLIDATED DASHBOARD"
echo "============================================================"

# Step 2: Generate the consolidated markdown report
python -m workflows.consolidated_exit_strategy

echo ""
echo "Workflow finished! Check portfolio/consolidated_exit_strategy.md"
