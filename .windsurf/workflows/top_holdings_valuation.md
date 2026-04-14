---
description: Expert LLM valuation — run top holdings valuation (50% coverage / top N) + ranking reports
---

# Top Holdings Valuation

Runs **sector-expert LLM valuations** for your top holdings from a portfolio snapshot and generates ranking reports.
Each company is valued using a sector-specific prompt from `prompts/sector_valuation/*.txt` with full company context (financials, master report, sector profile) injected and sent to the LLM.

## Prerequisite (recommended)

Start Chrome in debug mode to avoid Screener.in tarpit (needed for fetching financials/sector profiles).

```bash
bash scripts/launch_chrome_debug.sh
```

## Step 1 — Run expert valuation for top ~50% portfolio

Note: Omitting the `--date` flag uses the latest holdings snapshot.

// turbo
```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.top_holdings_valuation --user ZV3899 --coverage 50
```

## Step 2 — Outputs

- Per company:
  - `data/companies/{SYMBOL}/reports/valuation_agent.md`
  - `data/companies/{SYMBOL}/reports/valuation_agent.json`

- Snapshot reports:
  - `portfolio/ZV3899/holdings/<snapshot_date>/valuation_ranking.md` (only the processed top holdings)
  - `portfolio/ZV3899/holdings/<snapshot_date>/valuation_ranking_all_companies.md` (all companies that already have a valuation report)
