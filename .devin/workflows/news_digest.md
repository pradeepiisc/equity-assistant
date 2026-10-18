---
description: Daily news digest — Google News RSS headlines for top 20 holdings + watchlist + Super Investor & Anchor Radar
---

## Daily News Digest

Fetches headlines via Google News RSS for:
1. **🌟 Super Investor & Anchor Activity Radar (Last 30 Days):**
   - Automatically tracks IPOs, anchor allotments, pre-IPO stakes, and block/bulk deals by marquee investors:
     - **Ashish Kacholia** (*Lucky Securities / Bengal Finance & Investment*)
     - **Mukul Agrawal** (*Param Capital*)
     - **Madhusudan Kela** (*MK Ventures / Singularity AMC / Singularity Equity*)
     - **Vijay Kedia** (*Kedia Securities*)
     - **Sunil Singhania** (*Abakkus Asset Manager*)
2. **📊 Portfolio & Watchlist Company News:**
   - Top 20 holdings by current value (same-day headlines)
   - All stocks in `watchlist.yaml` (same-day headlines)

Uses Google News RSS — no API key needed, fast (~0.3s per item).
Headlines only, no full article scraping.

### Run

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest --user ZV3899
# For alternate account: --user WN5759
```

Output saved to `portfolio/{user}/news_digest.md`

### Custom number of holdings

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest --top 30 --user ZV3899
```

### Notes
- Part of Step 5 in `/daily_run`
- Headlines are linked to Google News (click to open)
- Super Investor Radar scans within a 30-day window to ensure IPO anchor participation and block deals are not missed
- Portfolio company news filters strictly for same-day items (Asia/Kolkata timezone against snapshot date)
