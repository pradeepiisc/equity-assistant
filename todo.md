# Automation Backlog

Things that can still be added to automate investment work further.
Priority = impact on daily decision-making vs effort to build.

---

## 🔴 High Value

- Calculating the valuation of the company that uses expertise skills like below, order book visibility, expansion plans, management quality, any moats, lumpsum or SIP
- Sector specific skills with expertise in 
    hospitals, pharma, fintech, chemicals, textile retail, agri commodities, infra, shrimps and fisheries, animal feeds, consumer durables, entertainment, electrical equipmemts, gems and jewellery non-gold and non-silver, 


- **Ace investor tracker** — Wire `investor_watchlist.yaml` into `investor_activity.py`. Currently `investor_activity.py` shows generic FII/DII totals. It should specifically flag when Kacholia / Kedia / Dolly Khanna / any tracked ace investor from `investor_watchlist.yaml` **enters or exits** any held or watchlist stock. The `shareholding_fetcher.py` already does this for `watchlist.yaml` stocks via Screener AJAX — just needs to be surfaced in the portfolio workflow.

- **Push alerts** — Currently all outputs are pull-based (you run a script, read a file). Add a lightweight notifier so that when a threshold is crossed (stock below 200 DMA, promoter selling >2pp, day drop >5%), a macOS notification or Telegram message is sent automatically. No need to check the report manually.

- **Rebalancing planner** — Given target sector allocations (e.g. "max 20% in any sector, cap any single stock at 4%"), compute specific ₹ BUY or SELL amounts needed to rebalance. Show top 5 buys and top 5 reduces for the next SIP cycle.

- **Quarterly auto-research** — During results season, auto-run `data_fetch` + `full_company_analysis` for every stock with allocation ≥ 1% (not just the 2 stocks in `watchlist.yaml`). Today you have to manually add stocks to `watchlist.yaml` to get a deep research report.

---

## 🟡 Medium Value

- **52-week low screener** — Alert when any holding hits a new 52-week low. Often the best SIP entry point for conviction stocks. Can be built using yfinance's `history(period="1y")` and comparing with `last_price`.

- **SIP execution tracker** — Track a target SIP amount per high-conviction stock (stored in `watchlist.yaml` or a separate `sip_plan.yaml`). Show which ones are "behind plan" vs actual invested. Useful for disciplined averaging.

- **P&L attribution by sector / theme** — "Power sector holdings contributed +₹X this week / this month." Needs historical holdings snapshots (already accumulating in `portfolio/ZV3899/`).

- **Valuation screener for watchlist** — For each `watchlist.yaml` stock, pull P/E, P/B, EV/EBITDA, revenue growth YoY from Screener and show a comparison table vs. their 3-year average. Quick sanity check on whether entry price is reasonable.

- **Sector map auto-updater** — Right now `sector_map.yaml` is manually maintained. When a new stock appears in holdings that isn't in the sector map, auto-lookup its sector from Screener.in and append it. Currently ~88 stocks show as "Unclassified".

---

## 🟢 Lower Value (Nice to Have)

- **Portfolio export to Excel** — Auto-generate a formatted `.xlsx` from the daily report data. Useful for sharing or for investors who prefer spreadsheets.

- **Historical report diffs** — Compare today's report to last week's to surface what changed: new alerts, resolved alerts, DMA crossings, position size changes.

- **Screener stock finder** — Run a custom Screener.in filter (high ROCE, low debt, small/mid cap) to surface new stock ideas outside the current portfolio.

- **Concall alert** — When a held or watchlist stock announces results (detectable via NSE/BSE RSS), auto-trigger `quarterly_review.py` for that stock.

- **Scheduler / cron setup** — Wire all daily workflows into a macOS launchd or a simple cron job so `/daily_run` happens automatically at market open (9:15am) without manual triggering.

---

## Notes

- Items in **🔴 High** should be tackled first — they directly impact buy/sell decisions.
- Items in **🟡 Medium** improve portfolio hygiene and discipline over time.
- Items in **🟢 Lower** are quality-of-life improvements.
- The infrastructure (skills, Kite integration, Screener scraping, DMA cache) is already in place for most of these. The main work is wiring them together.
