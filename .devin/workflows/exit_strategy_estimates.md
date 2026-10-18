---
description: Exit Strategy Estimates Workflow — Generate Forward Financial Projections
---

# Exit Strategy Estimates Workflow

This workflow automates the extraction of future guidance (`FY27`, `FY28`, `revenue`, `expansion`, `margins`) from Q4 FY26 / Mar 2026 transcripts and Screener Insights to build a forward-looking financial model. It calculates the fair value stock price ranges for FY27, FY28, and FY29 using mathematical projections based on management guidance, completely avoiding expensive LLM API calls.

## Steps

1. Find the Top 50 companies from the combined portfolio by current value.
2. Filter the companies to only include those with recent shareholding and transcript data (post March 2026).
3. Use `pdfplumber` to extract text from recent transcript PDFs.
4. Extract management guidance snippets using regex for keywords (`FY27`, `growth`, `margin`, `revenue`, `capacity`, `capex`, `EBITDA`).
5. Extract exact FY26 financial baselines (Sales, OPM, PAT, EPS) directly from `data/companies/{symbol}/financials/*_financials_*.txt` files to guarantee mathematically sound projections.
6. Extract explicit shareholding updates tracking Promoters, FIIs, and DIIs to verify institutional accumulation.
7. Parse the extracted snippets and true FY26 financials to project future revenues, margins, and estimated PAT for FY27, FY28, and FY29.
8. Derive EPS and Forward P/E multiples by mapping net profit trajectories against total outstanding shares.
9. Report the **Target Price Reasoning & Valuation**, explaining how current P/E compression over the years justifies fair values and providing transparent formula derivations for the target bands.
10. Output the generated Markdown report to `portfolio/forward_exit_analysis.md`.

To run the full end-to-end extraction and projection pipeline locally:

```bash
# Extract guidance for Top 5
.venv/bin/python3 extract_top5.py

# Extract guidance for the remaining eligible companies
.venv/bin/python3 extract_remaining.py

# Parse metrics for the remaining companies
.venv/bin/python3 parse_metrics.py

# Parse exact FY26 financial data from text dumps
.venv/bin/python3 parse_actual_financials.py

# Calculate estimates (including True EPS, Forward P/E, Shareholding, and Reasoning) for all companies
.venv/bin/python3 generate_final_report.py
```

### Full Pipeline Automation (One-Click)

// turbo
```bash
.venv/bin/python3 extract_top5.py && \
.venv/bin/python3 extract_remaining.py && \
.venv/bin/python3 parse_metrics.py && \
.venv/bin/python3 parse_actual_financials.py && \
.venv/bin/python3 generate_final_report.py
```
