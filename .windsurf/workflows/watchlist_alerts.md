---
description: Watchlist entry alerts — DMA-based buy/hold/wait signals for all stocks in watchlist.yaml
---

## Watchlist Entry Alerts

For every stock in `watchlist.yaml`, fetches 50+200 DMA and current price, then categorises:

| Signal | Condition |
|---|---|
| 🟢 Fresh Entry | Not held, price below 200 DMA — strong SIP start |
| 🔵 Accumulate | Not held, above 200 DMA but below 50 DMA — stagger in |
| 🟡 Watch | Not held, within ±20% of 200 DMA — no urgency |
| ⚠️ Extended | Not held, >+20% above 200 DMA — wait for pullback |
| ✅ Already Held | In portfolio — with add/hold signal based on DMA |

### Run

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.watchlist_alerts
```

Output saved to `portfolio/ZV3899/{today}/watchlist_alerts.md`

### Add new stocks to watchlist

Edit `watchlist.yaml`:
```yaml
stocks:
  - symbol: NEWSTOCK
    name: Full Company Name Ltd
    exchange: NSE
    sector: Sector Name
    screener_url: https://www.screener.in/company/NEWSTOCK/
    watch_reason: "Why you want to watch this"
```

The alerts workflow will automatically pick it up on next run.

### Notes
- DMA is fetched via `skills/dma_fetcher.py` (yfinance primary source, cached daily)
- For stocks already held, shows current allocation % and P&L%
- Part of the `/daily_run` workflow (Step 4)
