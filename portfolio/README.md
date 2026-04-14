# Portfolio — Zerodha Kite

Daily portfolio fetch for Pradeep Bansal (ZV3899).
Data is saved under `ZV3899/{YYYY-MM-DD}/` with 4 files:
- `holdings.json` / `holdings.csv` — all equity holdings
- `positions.json` — day + net positions
- `summary.txt` — P&L snapshot + watchlist stocks held

---

## What Gets Automated — Scenario Coverage

| # | Scenario | Status | Script | Frequency |
|---|---|---|---|---|
| 1 | Daily portfolio health — P&L, sizing, overview | ✅ Built | `portfolio_daily_report.py` | Daily |
| 2 | DMA-based reduce / average-up signals | ✅ Built | `portfolio_daily_report.py` | Daily |
| 3 | Tax loss harvesting candidates (₹ ranked) | ✅ Built | `portfolio_daily_report.py` | Daily |
| 4 | Sector over-concentration alerts | ✅ Built | `portfolio_daily_report.py` | Daily |
| 5 | High conviction SIP signals (🟢/🔵/🟡/⚠️) | ✅ Built | `portfolio_daily_report.py` | Daily |
| 6 | Top 20 holdings + cumulative concentration table | ✅ Built | `portfolio_daily_report.py` | Daily |
| 7 | **Daily movers** — today's % move per stock | ✅ Built | `portfolio_daily_report.py` | Daily |
| 8 | Watchlist entry price alerts (stocks not yet held) | ✅ Built | `watchlist_alerts.py` | Daily |
| 9 | News headlines for top holdings + watchlist | ✅ Built | `news_digest.py` | Daily |
| 10 | Shareholding / investor activity tracker | ✅ Built | `investor_activity.py` | Weekly |

### What's Still Pending (see `todo.md` in root)
| Priority | Item |
|---|---|
| 🔴 High | Ace investor tracker — cross-reference `investor_watchlist.yaml` with holdings |
| 🔴 High | Push alerts — macOS / Telegram when thresholds crossed |
| 🔴 High | Rebalancing planner — ₹ buy/sell to hit target sector weights |
| 🟡 Medium | 52-week low screener for conviction entries |
| 🟡 Medium | SIP execution tracker — planned vs actual per stock |
| 🟡 Medium | Sector map auto-updater for new holdings |

---

## Daily Workflow (run every market morning)

## Step 1 — Login (once per day)

The Kite access token expires daily. Run this **once each morning** in your terminal:

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --login
```

Your browser will open the Kite login page. After you log in, the script
automatically captures the token (browser shows "Login successful!") and
saves it to `.env`. No copy-paste needed.

> **Prerequisite (one-time):** In your Kite developer console
> (https://developers.kite.trade/apps), set the app's **Redirect URL** to:
> `http://127.0.0.1:8765/`

---

## Step 2 — Save portfolio

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --save-portfolio
```

Creates `portfolio/ZV3899/YYYY-MM-DD/` with all four files.

---

## Other useful commands

```bash
# Print holdings table in terminal
python -m integrations.kite_connect --holdings

# Print portfolio summary (P&L + watchlist stocks)
python -m integrations.kite_connect --summary

# Raw JSON output (pipe to jq etc.)
python -m integrations.kite_connect --holdings --json
python -m integrations.kite_connect --summary --json
```
