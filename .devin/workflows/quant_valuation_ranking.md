---
description: Quantitative valuation ranking — score all companies on 5 dimensions and generate master ranking report
---

# Quantitative Valuation Ranking

Runs the **automated quantitative scoring** for all companies in `data/companies/` and generates:
- Individual valuation reports per company (`data/companies/{SYMBOL}/reports/valuation_summary.md`)
- Master ranking report (`data/valuation/master_ranking.md` + `.json`)

No LLM calls needed — purely data-driven from financials, shareholding, and key ratios.

## Scoring Methodology

Each company scored on **5 dimensions** (0–5 scale, weighted composite):

| Dimension | Weight | Key Metrics |
|---|---|---|
| Growth | 25% | Sales growth 3Y CAGR, Profit growth 3Y CAGR, Quarterly revenue/profit trend |
| Profitability | 20% | ROCE, ROE, Operating Profit Margin |
| Valuation | 20% | P/E, PEG ratio, P/BV, EV/EBITDA |
| Balance Sheet | 15% | Debt/equity, Current ratio, OCF/FCF, Pledging |
| Shareholding | 20% | Promoter % and trend, FII/DII trends, Smart money (investor_watchlist.yaml) |

**Rating scale:** STRONG BUY (≥4.0), BUY (≥3.5), ACCUMULATE (≥3.0), HOLD (≥2.5), WATCH (≥2.0), REDUCE (≥1.5), AVOID (<1.5)

## Data Sources

- `data/companies/{SYMBOL}/financials/*.txt` — Key ratios, P&L, Balance Sheet, Cash Flow, Historical Ratios
- `data/companies/{SYMBOL}/shareholding/*.txt` — Quarterly holdings, Individual holders, Tracked investor alerts
- `data/companies/{SYMBOL}/meta.json` — Company name, sector, Screener URL
- `investor_watchlist.yaml` — Tracked investor names for smart money detection

## Step 1 — Run Valuation Script

// turbo
```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python scripts/generate_valuation_report.py
```

## Step 2 — Review Outputs

- Master ranking: `data/valuation/master_ranking.md`
- Master JSON: `data/valuation/master_ranking.json`
- Individual reports: `data/companies/{SYMBOL}/reports/valuation_summary.md`

## Step 3 — Optional: Re-fetch Missing Data First

If companies are missing financials or shareholding data, fetch via Chrome CDP before re-running:

```bash
# Start Chrome debug mode
bash scripts/launch_chrome_debug.sh

# Fetch all data for specific company
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.data_fetch --symbol SYMBOLNAME
```

## Key Features

- **Deduplication**: If two symbols share the same company name (e.g., BSE vs NSE listing), keeps the one with higher market cap
- **Smart money detection**: Normalizes investor names (Pvt/Private, Ltd/Limited) for fuzzy matching against `investor_watchlist.yaml`
- **Fallback growth**: Uses computed revenue/profit CAGR when Key Ratio growth fields are missing
- **Section-aware parsing**: Only reads "Key Ratios (Current)" section, ignoring historical ratios

## Notes

- ~12 companies may be skipped if they lack financial data entirely
- Smart money detection requires the shareholding file to have "Individual Holder Detail" section (fetched via Screener AJAX)
- Companies without shareholding data get 0 for the shareholding dimension
