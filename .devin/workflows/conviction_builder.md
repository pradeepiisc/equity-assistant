---
description: Conviction review — score held positions with Add/Hold/Trim/Exit recommendations
---

## Conviction Builder

Synthesises DMA position, financials, growth signals, and transcripts into an actionable conviction review for held stocks.

### Step 1 — Run for specific symbols
```
python -m workflows.conviction_builder --symbols SYMBOL1 SYMBOL2
```

### Step 2 — Run for top N daily losers automatically
```
python -m workflows.conviction_builder --top-losers 5
```

### Step 3 — Combine both (losers + specific stocks)
```
python -m workflows.conviction_builder --top-losers 5 --symbols AURIONPRO BETA
```

### Step 4 — Specify user if multiple Kite accounts exist
```
python -m workflows.conviction_builder --symbols AURIONPRO --user ZV3899
```

Output is saved to: `portfolio/{user}/holdings/{date}/conviction_review.md`

### Notes
- If a stock has no existing analysis (`data_quality: thin`), the report will suggest running `python -m workflows.full_company_analysis SYMBOL` to enrich.
- DMA data is loaded from the daily cache (`portfolio/.cache/dma_{date}.json`). Run the daily report first to populate it.
- Works for any symbol in `watchlist.yaml` or `portfolio_companies.yaml` that is currently held.
