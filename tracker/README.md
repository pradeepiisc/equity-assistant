# Stock Tracker

A dashboard + alert engine for every stock in the portfolio (both Kite accounts), the
watchlist, and stocks named in your research notes. It pulls **official exchange files**
on a schedule, detects milestones and corporate events, and pushes the important ones as
macOS notifications. It reuses — and never rewrites — the research the existing workflows
produce (concall analysis, master reports, valuations, Screener files).

```
bash scripts/run_tracker.sh                 # dashboard → http://127.0.0.1:7788 (polls while open)
bash scripts/tracker_schedule.sh install    # keep polling when it is closed (macOS LaunchAgent, every 3 min)
```

**Polling.** The dashboard runs one scheduler pass (`tick`) at start-up and then every 3 minutes;
the LaunchAgent does the same while the dashboard is closed. A lock file
(`data/tracker/tick.lock`) lets only one pass run at a time, and each job inside a pass runs
only when it is due, so two pollers never double the requests sent to NSE. The Status page
shows both pollers, every feed's cadence, the last ticks and the job log, and installs or
removes the LaunchAgent. On first install macOS may ask to let `python3.11` read the
Documents folder — until that is allowed the agent's ticks hang (the Status page says so).

## What it tracks

| Area | Source | Cadence |
|---|---|---|
| Prices, DMA 20/40/50/100/200, 52-week range, volume | NSE + BSE bhavcopy (UDiFF), all boards incl. SME | daily, after 18:15 |
| Split / bonus adjustment | NSE PR archive `Bc*.csv` ex-dates; BSE-only stocks inferred from gaps no circuit band allows | daily |
| Trend phase | price vs DMA40 vs DMA100 — same rule as `workflows/shared_sections.classify_trend` | daily |
| Crossovers | close × DMA40 / DMA200 (0.5% confirmation), golden / death cross (DMA50 × DMA200), entered Strong Bullish / Bearish, 52-week high/low, volume spikes | daily |
| Filings: order wins (with ₹ value and % of TTM revenue), capacity expansion, fund raising (preferential / QIP / rights / warrants), acquisitions, results, concall transcripts & meets, board meetings, credit ratings, auditor / KMP resignations, litigation | NSE `Online_announcements` RSS, classified by SEBI LODR subject | every 3 min on weekdays 09:00–18:00, else every 10 min, 07:00–23:30 |
| SAST Reg 29 (substantial stake changes), Reg 31 pledges, encumbrance, insider (PIT), shareholding filings, corporate actions | NSE RSS feeds | every 10 min |
| Bulk & block deals — tagged FII / DII / fund / promoter / your tracked investors / prop-HFT | NSE `bulk.csv`, `block.csv` | daily, after 17:15 |
| Quarterly shareholding, tracked-investor entries/exits | Screener files from `workflows.earnings_season` / `data_fetch` | when those files change |
| Named holders — anchor investors (non-institutional ≥1%), FIIs, DIIs, promoters — by quarter, with new / exited flags and events | NSE shareholding-pattern XBRL (linked from the SHP RSS item) for NSE stocks; Screener files for history and BSE-only stocks | as filed |
| Quarterly revenue, EBITDA, PAT, EPS, share capital | NSE results XBRL (linked from the Integrated_Filing_Financials RSS item) | as filed |
| Order book, order inflow, individual orders, revenue / margin / EPS guidance, capex, tailwinds, headwinds, policy, interest-rate sensitivity — each with its verbatim quote, page and PDF | attachment PDFs of tracked stocks' NSE filings, read by gpt-4.1 within a daily budget; PDFs already on disk as back-fill | as filed |
| BSE-only stocks: filings, financials, shareholding | Screener page through the Chrome debug session (≤30 a day, ≥9 s apart, stops on the first block); PDFs straight from BSE | when due |
| Concall guidance, guidance-vs-delivery, capex, KPIs, order book, decision framework | existing `reports/*.json` + `insights_values.json` | read on demand |
| Your notes | `notes/*.md` (see `notes/README.md`) | on change |

All data is public and fetched with an honest user agent, one request per second per host.
Everything is stored locally in `data/tracker/` (git-ignored): `tracker.db` plus the raw
exchange files, so a newly added stock is back-filled without downloading again.

## Order books, orders, priorities (phase 2)

- **Order books** — every tracked stock with an order book on record: growth over 3M / 6M / 1Y
  (latest figure vs one at least that far back — "—" when there is none), order book,
  revenue (TTM, source named), book/revenue, market cap, history sparkline. Click a sparkline
  for the quarterly history with the sentence each figure came from and a link to its PDF page.
- **Orders** — by company (orders as % of revenue over 3M / 6M / 1Y, expandable to each order:
  customer, type, value, duration, annual value) or as a chronological feed.
- **Priorities** — expected revenue for the next twelve months (from guidance, else the order
  book over its execution period, else the trailing trend), forward EPS and P/E, and a
  rule-based Add / Hold / Trim / Exit / Watch with its reasons. Weights live in `tracker.yaml`;
  a note with add / reduce / exit overrides the action. Decision support, not an order.

**What the LLM is allowed to do.** Only `documents.py`/`extract.py` call it, only for new or
unread PDFs of tracked stocks, only on pages whose keywords matter, at most once per PDF (the
response is cached by file hash), within `llm.daily_token_budget` (back-fill capped at 60% of
it). Every call is logged and the Status page shows today's tokens and ₹ estimate. Every
extracted figure is checked against the PDF text: ✓ verbatim, ≈ approximate (near match, or
number and term on the same slide), ✗ not found — the last are kept but never plotted or scored.
An order-book figure whose quote never says "order book" (bids, inflow, revenue) is rejected.

## Alerts

Every detected item is an *event*. New events at **medium+** go to the dashboard inbox;
**high** ones are pushed as macOS notifications (more than 4 at once → one summary;
held during quiet hours 23:00–07:00). Severity is importance, not direction — the ▲/▼ glyph
shows whether the event is positive or negative. Thresholds live in `tracker.yaml`.

High by default: DMA200 crosses, golden/death cross, order wins ≥ 5% of TTM revenue (or
≥ ₹100 Cr), equity fund raising, SAST Reg 29, pledges, auditor/KMP resignations, rating
downgrades, promoter holding moves ≥ 1 pp, tracked investors entering/exiting, promoter or
tracked-investor bulk deals, institutional deals ≥ ₹10 Cr, and your note's entry/target/stop
levels being reached.

## CLI

```
python -m tracker init           # exchange master data + universe
python -m tracker prices         # bhavcopy back-fill / daily load (+ corporate actions)
python -m tracker signals        # indicators, phases, technical events, note levels
python -m tracker feeds          # NSE filing feeds
python -m tracker deals          # bulk / block deals
python -m tracker shareholding   # Screener shareholding history
python -m tracker notes          # re-index notes/ and the universe
python -m tracker alerts         # inbox + notifications
python -m tracker tick [--force] # one scheduler pass (what the LaunchAgent runs; --force polls every feed)
python -m tracker docs [--seconds 600] [--no-llm]   # filings → PDFs → extraction, within the budget
python -m tracker results        # quarterly numbers from NSE results XBRL
python -m tracker bse            # one due BSE-only company via Screener (needs the Chrome debug session)
python -m tracker status
python -m tracker serve [--no-poll]   # dashboard (also mounts the old workflow launcher at /legacy)
```

## Code map

| Module | Role |
|---|---|
| `refdata.py` | NSE mainboard + Emerge lists, BSE scrip master |
| `universe.py` | holdings ∪ watchlist ∪ notes → resolved stocks (ISIN, NSE symbol, BSE code, research folder) |
| `prices.py`, `corpactions.py` | bhavcopy download/cache/load; split/bonus-adjusted series |
| `technicals.py` | indicators, phase rule, crossover events |
| `feeds.py`, `classify.py` | NSE RSS polling, company matching, filing taxonomy, ₹ value extraction |
| `deals.py`, `shareholding.py` | bulk/block deals with client tagging; quarterly holding history |
| `notes.py` | notes parsing, stance + price levels, level alerts |
| `research.py` | reads existing LLM reports for the stock page |
| `alerts.py` | inbox + macOS notifications |
| `jobs.py`, `scheduler.py`, `__main__.py` | orchestration, tick lock, due-feed cadence, server poll loop, LaunchAgent control, CLI |
| `holders.py` | named holders from NSE SHP XBRL and Screener; anchor / tracked-investor events |
| `financials.py` | NSE results XBRL → quarterly revenue, EBITDA, PAT, EPS |
| `documents.py`, `extract.py` | filing PDFs → relevant pages → LLM extraction with quote checks; insights back-fill |
| `metrics.py`, `score.py` | order-book series and growth, revenue / market cap, forward estimates, Add/Reduce score |
| `bse_fallback.py` | BSE-only stocks via Screener (Chrome session) + BSE PDFs |
| `api.py`, `api_orders.py`, `server.py`, `web/` | dashboard API and UI (`orders.js`, `priorities.js`, `status.js` are phase 2) |

## Known gaps

- **BSE-only stocks** (29 today) get filings only through the Screener fallback, which needs the
  Chrome debug session (`scripts/launch_chrome_debug.sh`); BSE's announcement API refuses
  non-browser clients (HTTP 403). Without the session they still get prices and technicals.
- **Order-book history** starts thin: it fills as the ~700 PDFs already on disk are read within
  the daily budget (about two to three weeks) and as new filings arrive.
- **Financials** for growth and margins still come from the Screener files until a stock has
  eight quarters of NSE results XBRL.
- NSE's RSS feeds are rolling windows. Filings published while the Mac is asleep for long
  stretches can be missed; bulk/block deals for a missed evening are not recoverable
  (NSE only publishes the latest day).
- SAST / pledge items name the party but not the direction or size — those are inside the
  attached PDF (next phase).
- Concall insights are only as fresh as the last `transcript_analysis` run.
