---
description: Watchlist gap analysis — find which sectors are missing from your portfolio, top 3 picks from watchlist
---

# Watchlist Gap Analysis

Cross-references your portfolio's sector coverage against all companies in `watchlist.yaml`.  
Identifies sectors that are **absent or thin** in your portfolio and recommends the **top 3 watchlist picks** that would add the most meaningful diversification.

**Prerequisite:** Sector analysis profiles must be cached. Run sector analysis first if stale (>30 days).

## Step 1 — (If needed) Refresh sector analysis profiles

```bash
bash scripts/run_sector_analysis.sh
```

## Step 2 — Run watchlist gap analysis

```bash
bash scripts/run_watchlist_gap_analysis.sh
```

**Output:** `portfolio/ZV3899/watchlist_gaps/{today}/watchlist_gap_analysis.md`

The report contains:
- **Portfolio sector assessment** — where you're concentrated and what's missing
- **Gaps table** — sectors in watchlist but absent/thin in portfolio
- **Top 3 picks** — ranked by: sector uniqueness × investment rating (Buy preferred)
- **Full sector coverage map** — all portfolio sectors for reference

## Step 3 — (Optional) Review master reports for the top picks

```bash
# Read the analysis for a specific watchlist company
cat data/companies/SYMBOL/reports/master_report.md
```
