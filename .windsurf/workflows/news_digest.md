---
description: Daily news digest — Google News RSS headlines for top 20 holdings + watchlist
---

## Daily News Digest

Fetches 3 recent headlines per stock for:
- Top 20 holdings by current value
- All stocks in `watchlist.yaml`

Uses Google News RSS — no API key needed, fast (~0.3s per stock).
Headlines only, no full article scraping.

Important: it now keeps **only same-day headlines** for the latest holdings snapshot date
(e.g., snapshot `2026-03-02` ⇒ only headlines published on `2026-03-02` IST).
If no headlines match that date, stock section will be empty.

### Run

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest
```

Output saved to `portfolio/ZV3899/holdings/<snapshot_date>/news_digest.md`

### Custom number of holdings

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest --top 30
```

### Notes
- Part of the optional Step 5 in `/daily_run`
- Headlines are linked to Google News (click to open)
- Useful for quickly spotting earnings results, regulatory news, or management changes
- For in-depth article fetching, use `skills/news_fetcher.py` with a specific symbol
- Date matching is done in Asia/Kolkata timezone against the portfolio snapshot date
