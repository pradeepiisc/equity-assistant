---
description: Fetch Zerodha Kite portfolio (login + save holdings to dated folder)
---

## Fetch Portfolio from Zerodha Kite

Run these two commands sequentially in a terminal. Step 1 opens your browser for login; Step 2 saves the data.

### Step 1 — Login (required once per day)

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --login
```

- Browser opens Kite login automatically
- Log in with your Zerodha ID + password + TOTP
- Browser shows "Login successful!" — access token is saved to `.env`

### Step 2 — Save portfolio

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --save-portfolio
```

Saves to `portfolio/ZV3899/{today's date}/`:
- `holdings.json` — full holdings with P&L per stock
- `holdings.csv` — same data in CSV (Excel-friendly)
- `positions.json` — day and net intraday positions
- `summary.txt` — total invested, current value, P&L, watchlist stocks held

### Verify

```bash
cat portfolio/ZV3899/$(date +%Y-%m-%d)/summary.txt
```
