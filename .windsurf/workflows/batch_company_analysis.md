---
description: Batch company analysis — run data_fetch + full LLM analysis for all or selected watchlist companies
---

# Batch Company Analysis

Fetches all raw data (transcripts, news, shareholding, financials) **and** runs the full 5-skill LLM analysis for every company in `watchlist.yaml`. Produces a `master_report.md` with investment rating, thesis, catalysts, risks, and monitoring triggers per company.

**Prerequisite:** Chrome debug session recommended for Screener scraping.
```bash
bash scripts/launch_chrome_debug.sh
```

## Step 1 — Run batch for all watchlist companies (skip already-analysed)

```bash
bash scripts/run_batch_analysis.sh --skip-existing
```

## Step 2 — (Optional) Force re-run for specific symbols

```bash
bash scripts/run_batch_analysis.sh --symbols ZENTEC KAYNES TARIL
```

## Step 3 — (Optional) Data fetch only (no LLM analysis)

```bash
bash scripts/run_batch_analysis.sh --data-only
```

## Step 4 — (Optional) Analysis only (data already fetched)

```bash
bash scripts/run_batch_analysis.sh --analysis-only
```

**Output:** `data/companies/{SYMBOL}/reports/master_report.md` for each company.

**Runtime:** ~5–10 min per company (LLM calls). Full 23-company batch ≈ 2–4 hours.
