---
description: Generate daily portfolio health report (200 DMA, sector map, sizing alerts, tax harvest)
---

## Daily Portfolio Health Report

Prerequisite: portfolio must be saved for today. If not done yet, run `/fetch_portfolio` first.
For the full combined morning workflow (fetch + report), use `/daily_run`.

### Full report with DMA

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report
```

Fetches 50+200 DMA via yfinance (free) for: watchlist + top 15 losers + ≥4% alloc + top 20 by value + all >1% alloc (high conviction). Results cached per day.
Report saved to `portfolio/ZV3899/{today}/daily_report.md`.

### Quick run without DMA (faster)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report --no-dma
```

### What the report contains

| Section | Purpose |
|---|---|
| **Overview** | Invested / current value / total P&L |
| **Position Sizing** | Alert if any stock ≥5% of portfolio (with ₹ to reduce) |
| **DMA Status** | 🔴 below 200 DMA (reduce) / 🟢 >+10% above (avg-up) with both 50+200 |
| **Top 20 Holdings** | Biggest positions with invested, current, 50 DMA, 200 DMA, vs-200% |
| **Portfolio Concentration** | Cumulative % table sorted by value — see which N stocks = 50/70/90% |
| **High Conviction SIP Signals** | 🟢 Entry / 🔵 Accumulate / 🟡 Hold / ⚠️ Overextended for >1% or watchlist stocks |
| **Sector Concentration** | Allocation % and P&L by sector |
| **Top 10 Gainers / Losers** | With 200 DMA status for losers |
| **Tax Loss Harvest** | All losing stocks sorted by ₹ loss |
| **Watchlist Overlap** | Watchlist stocks currently held |

### High conviction definition
Stocks with **allocation ≥ 1%** OR in `watchlist.yaml`. Update `watchlist.yaml` to add fresh-entry candidates not yet held.

### DMA source priority
`local snapshots → yfinance (.NS/.BO) → Screener.in → Tickertape → Chrome CDP (Trendlyne)`

### Daily evolving files

In addition to the dated `daily_report.md`, the workflow writes evolving files to `portfolio/{user}/`:

| File | What it tells you |
|---|---|
| **dma_deployment.md** | All holdings with DMA40/DMA100 trend classification (Strong Bullish → Bearish) |
| **sector_allocation.md** | Allocation % per sector with bullish/bearish trend count |
| **deployment_candidates.md** | Bullish + under-allocated (< 2%) — where to put money |
| **caution_list.md** | Bearish + significant allocation (≥ 0.5%) — consider reducing |
| **watchlist_not_held.md** | DMA status for watchlist stocks you don't own yet |
| **sme_summary.md** | All SME stocks with trend + allocation |

These are overwritten each run and provide a live dashboard view.

### Watchlist auto-sync

The daily report automatically removes portfolio-held stocks from `watchlist.yaml` on each run.

### Adding sector labels
Edit `sector_map.yaml`:
```yaml
YASHO: "Chemicals"
VAIBHAVGBL: "Consumer"
```
Re-run `--no-dma` to see updated sector table.
