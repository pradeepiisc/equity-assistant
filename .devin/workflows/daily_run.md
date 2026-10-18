---
description: Full daily morning run — fetch portfolio from Kite then generate health report
---

## Daily Morning Run

> **CRITICAL EXECUTION RULE — LOGIN FIRST, ALWAYS:**
> You **MUST** execute Step 1 (Login) **before any other step**, every single time this workflow is run, for **any** account. Kite access tokens expire daily at ~6:00 AM IST. **Do NOT skip login.** Do NOT proceed to Step 2 or any subsequent step until login is confirmed successful. This applies to all accounts (ZV3899, WN5759, or any future account).

Runs the complete daily workflow in sequence:
1. **LOGIN FIRST (MANDATORY)** — Kite access tokens expire every morning at 6:00 AM IST — login MUST happen before anything else
2. **Save today's portfolio snapshot**
3. **Generate daily health report** (DMA, position sizing, MTH/MTL, watchlist overlap)
4. **Watchlist entry alerts**
5. **News digest & Super Investor Radar** (Company news + Ashish Kacholia, Mukul Agrawal, Madhusudan Kela, Vijay Kedia, Sunil Singhania IPO/stake tracking)
6. **Portfolio Momentum Rotation** (Anchor alignment / DMA trend factor)
7. **Senior Equity Researcher Synthesis**

**Multi-user support:** Add `--user <ID>` to any command to use a different Kite account (e.g., `--user WN5759` or `--user ZV3899`).
Without `--user`, defaults to credentials in `.env` and auto-detects folder from Kite profile.

---

### Step 1 — Login (MANDATORY — ALWAYS RUN FIRST)

> **CRITICAL:** Zerodha Kite access tokens expire daily at ~6:00 AM IST. You **MUST** run this login step **FIRST** every time, for every account, before attempting any other step. **Never skip this step.** Never assume a previous session's token is still valid.

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --login --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

- Browser opens Kite login automatically
- Log in with Zerodha ID + password + TOTP
- Browser shows "Login successful!" — access token saved to `.env`
- Uses `KITE_API_KEY`, `KITE_API_SECRET` (or `KITE_API_KEY_<USER>`, `KITE_API_SECRET_<USER>` if `--user` specified)
- **Do NOT proceed to Step 2 until login is confirmed successful**

### Step 2 — Save portfolio snapshot (Run immediately AFTER login)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m integrations.kite_connect --save-portfolio --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

Saves to `portfolio/{user_id}/{today}/`: holdings.json, holdings.csv, positions.json, summary.txt
(Folder name auto-detected from Kite profile, e.g., ZV3899 or WN5759)

### Step 3 — Generate daily report

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

Report saved to `portfolio/{user_id}/{today}/daily_report.md`

**Cascade Instruction:** Read the generated `portfolio/{user_id}/{today}/daily_report.md`. Adopt the persona of a **Senior Equity Researcher**. Extract insights regarding price trends (e.g., DMA crossovers), allocation percentages (flag if invested allocation >5%, flag yellow if present value >15%, flag red to book profits if present value >20%), and sector exposure. Keep these insights in your context.

### Step 3 (alt) — Quick report without DMA (faster, no network)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.portfolio_daily_report --no-dma --user <USER_ID>
```

### Step 4 — Watchlist entry alerts

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.watchlist_alerts --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

For each stock in `watchlist.yaml`: fetches DMA and categorises as 🟢 Entry / 🔵 Accumulate / 🟡 Watch / ⚠️ Extended.
Saved to `portfolio/{user_id}/{today}/watchlist_alerts.md`

**Cascade Instruction:** Read `portfolio/{user_id}/{today}/watchlist_alerts.md`. Continuing as a **Senior Equity Researcher**, extract insights on watchlist stocks. Identify which top conviction ideas are flashing 'Entry' or 'Accumulate'. Store these insights in your context.

### Step 5 — News digest & Super Investor Radar (~10-30s)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_digest --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

Fetches:
1. **🌟 Super Investor & Anchor Activity Radar (Last 30 Days):** Automated search engine tracking for new IPOs, anchor allotments, pre-IPO stakes, and bulk/block deals by marquee investors:
   - **Ashish Kacholia** (*Lucky Securities / Bengal Finance & Investment*)
   - **Mukul Agrawal** (*Param Capital*)
   - **Madhusudan Kela** (*MK Ventures / Singularity AMC / Singularity Equity Fund*)
   - **Vijay Kedia** (*Kedia Securities*)
   - **Sunil Singhania** (*Abakkus Asset Manager*)
2. **📊 Portfolio & Watchlist Company News:** Same-day Google News RSS headlines for top holdings + watchlist.

Saved to `portfolio/{user_id}/{today}/news_digest.md`

**Cascade Instruction:** Read `portfolio/{user_id}/{today}/news_digest.md`. Analyze the news items for the day as a **Senior Equity Researcher**. Extract actionable insights from material company news AND highlight any new IPOs, anchor book participations, or fresh stakes taken by tracked superstar investors. Store these insights in your context.

### Step 6 — Portfolio Momentum Rotation (Anchor / DMA Fallback)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.anchor_momentum_rotation --user <USER_ID>
# Replace <USER_ID> with the target account, e.g.: ZV3899, WN5759
```

Combines either "simple relativeness" from anchor alignment files OR current portfolio allocations (if anchor data is missing) with the trend factor (LTP vs 40-DMA vs 90-DMA). It provides an exact list of over-allocated positions to exit/reduce and under-allocated positions to increase.
Saved to `portfolio/{user_id}/{today}/momentum_rotation.md`

**Cascade Instruction:** Read `portfolio/{user_id}/{today}/momentum_rotation.md`. As a **Senior Equity Researcher**, extract insights regarding momentum rotation. Identify over-allocated and under-allocated positions based on trend analysis. Store these insights in your context.

### Step 7 — Final Summary

**Cascade Instruction:** After completing Steps 1-6, output a **Summary** to the user in the chat.
As a **Senior Equity Researcher**, consolidate the insights from the daily report, watchlist alerts, news digest (including Super Investor Radar), and momentum rotation. 
Provide a clear summary of key observations:
1. **Portfolio Health**: DMA trends, allocation observations, and daily movers.
2. **Super Investor Radar & Watchlist News**: New IPO/anchor investments from Ashish Kacholia, Mukul Agrawal, Madhusudan Kela, Vijay Kedia, Sunil Singhania, and material portfolio company news.
3. **Momentum Trends**: Key trend observations from the rotation analysis.

---

### What the report covers

| Section | What it tells you |
|---|---|
| **Overview** | Invested / current value / total P&L |
| **Portfolio Concentration** | Cumulative % table — see how many stocks = 50%/70%/90% |
| **Top 10 Daily Movers** | Today's price move % vs previous session (not total P&L) |
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
