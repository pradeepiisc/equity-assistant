---
description: Full daily morning run — fetch portfolio from Kite then generate health report
---

## Daily Morning Run

Runs the complete daily workflow in sequence:
1. Login to Kite + save today's portfolio snapshot
2. Generate daily health report (DMA, SIP signals, tax harvest, watchlist, etc.)

**Multi-user support:** Add `--user <ID>` to any command to use a different Kite account (e.g., `--user WN5759`).
Without `--user`, defaults to credentials in `.env` and auto-detects folder from Kite profile.

---

### Step 1 — Login (once per day, opens browser)

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --login
# For alternate account: --login --user WN5759
```

- Browser opens Kite login automatically
- Log in with Zerodha ID + password + TOTP
- Browser shows "Login successful!" — access token saved to `.env`
- Uses `KITE_API_KEY`, `KITE_API_SECRET` (or `KITE_API_KEY_<USER>`, `KITE_API_SECRET_<USER>` if `--user` specified)

### Step 2 — Save portfolio snapshot

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --save-portfolio
# For alternate account: --save-portfolio --user WN5759
```

Saves to `portfolio/{user_id}/{today}/`: holdings.json, holdings.csv, positions.json, summary.txt
(Folder name auto-detected from Kite profile, e.g., ZV3899 or WN5759)

### Step 3 — Generate daily report

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report --user <USER ID>
# For alternate account: --user WN5759
```

Report saved to `portfolio/{user_id}/{today}/daily_report.md`

### Step 3 (alt) — Quick report without DMA (faster, no network)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report --no-dma --user <USER ID>
```

### Step 4 — Watchlist entry alerts

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.watchlist_alerts
# For alternate account: --user WN5759
```

For each stock in `watchlist.yaml`: fetches DMA and categorises as 🟢 Entry / 🔵 Accumulate / 🟡 Watch / ⚠️ Extended.
Saved to `portfolio/{user_id}/{today}/watchlist_alerts.md`

### Step 5 — News digest (optional, ~10-30s)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest
# For alternate account: --user WN5759
```

Fetches 3 headlines per stock for top 20 holdings + watchlist from Google News RSS.
Saved to `portfolio/{user_id}/{today}/news_digest.md`

---

### What the report covers

| Section | What it tells you |
|---|---|
| **Overview** | Invested / current value / total P&L |
| **Position Sizing** | Alerts if any stock ≥5% of portfolio (with ₹ to reduce) |
| **DMA Status** | 🔴 below 200 DMA (reduce) / 🟢 >+10% above DMA (avg-up) |
| **Top 20 Holdings** | Your biggest positions with 50+200 DMA and vs-200 column |
| **Portfolio Concentration** | Cumulative % table — see how many stocks = 50%/70%/90% |
| **High Conviction SIP Signals** | 🟢 Entry / 🔵 Accumulate / 🟡 Hold / ⚠️ Overextended per conviction stock |
| **Top 10 Daily Movers** | Today's price move % vs previous session (not total P&L) |
| **Tax Loss Harvest** | All losing stocks sorted by ₹ loss (105 stocks, ~₹29.5L harvestable) |
| **Watchlist Overlap** | Which watchlist stocks you currently hold |

---

### High conviction definition (auto-computed)
Stocks with **current allocation ≥ 1%** OR listed in `watchlist.yaml`.
Update `watchlist.yaml` to add fresh-entry candidates not yet held.

### DMA source priority
`yfinance → Chrome CDP (Trendlyne)`
Results cached daily in `portfolio/.cache/dma_YYYY-MM-DD.json`.

### Sector analysis (not in daily report)
Sector analysis is a **separate weekly workflow** — use `/sector_analysis` slash command.
It uses LLM + Screener.in to build rich company profiles (output industries, input sensitivity)
and caches per company for 30 days. Much more accurate than a static sector label.

### Daily Movers section
Requires at least 2 days of portfolio snapshots. On day 1, falls back to total P&L ranking.
