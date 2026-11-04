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

## Companies: results, estimates, bear / base / bull (since 2026-11-02)

The **Companies** page replaces Stocks, Priorities and the per-company notes list (old links still work).
Portfolio · Watchlist & exited · All. One row per company: price, day move and DMA40 / DMA200 position;
revenue bars (reported solid, estimates hatched); the latest period with YoY; next two fiscal years'
revenue and EPS; the order book with its bars and book ÷ revenue; bear / base / bull prices with the
move from today; and the score's action (held) or a watch signal (not held — Consider / Improving /
Wait, from the latest results' tone, the DMA position and the base-case upside). Click a row (or open
the stock's **Financials & estimates** tab) for the model:

- **Screener-style tables**, quarters (half-years for SME companies that report half-yearly) and
  fiscal years: revenue, YoY, EBITDA, margin, PAT, PAT YoY, EPS — eight reported periods, then the
  next eight as estimates (shaded), then the order book per period and the company's own operating
  KPIs from its concall insights (`insights/insights_values.json`: beds, rooms, capacity, utilisation …).
- **History** (`model.py`): the company's Screener file, NSE results XBRL on top (a quarter missing
  from the feed is the year-to-date figure less the next quarter), then the read results filings for
  any gap. NSE's per-company results lists fill each held / watched company's last nine quarters once
  (integrated filings from 2025, the older format before; `financials.backfill_history`).
- **Estimates**: from the valuation memo when there is one, else a rule — revenue from full-year
  guidance (a quarter's or a half's target, or one beyond 2.5× last year, is not used), else the order
  book over its execution period (an assumed period can't drive growth beyond the company's trend
  + 10 pts), else the trend; the rest of the current year never below the year-to-date run-rate; a
  margin target only for the year it names, stepped in from the typical recent margin (one-off
  periods excluded); PAT at the typical EBITDA→PAT conversion; EPS on the share count that agrees
  with the latest reported PAT ÷ EPS; split over periods by the company's seasonality. EBITDA
  excludes exceptional items; a filing keyed in the wrong unit is dropped.
- **Bear / base / bull** on next-year EPS × P/E. The base multiple is the median of today's P/E (trailing
  PAT ÷ shares), the stock's own three-year band and its sector's median among tracked companies,
  kept within 0.8–1.25× today's; bull at most 1.6× today's; EPS at most 3× trailing within two years;
  no price when earnings are too small or negative for P/E (banks get EPS but are best read on book
  value). The memo's multiples are used when it has them (the base within 0.6–2× today's P/E), every
  price recomputed as EPS × multiple with the EPS of the table.
- **The valuation memo** (`valuation.py`) runs your analyst prompt — the commitment & pipeline
  tracker, macro & regulatory transmission, the 3- and 5-year matrix, the risk dashboard (section 4,
  allocation, is left out) — over everything the tracker holds for the company, and returns the
  projections the table uses. Written for every held and watched company once
  (`python -m tracker valuations --all`), then rewritten automatically when new results, a concall
  note, an order-book figure, guidance or a capex fact arrives (2 per tick), or after 120 days; a
  button on the company rewrites it on demand. About ₹3–5 per memo.

**Watchlist & exited.** `watchlist.yaml` holds your list (replaced on 2026-11-02 from the Zerodha
watchlist; the previous one is `watchlist_previous_2026-11-02.yaml`; `group:` keeps Zerodha's group
names, `watch_reason:` your note, which the memo reads). A stock that leaves every Kite account is
recorded as exited (`exits` table, on the next holdings save) and stays watched for a year.

**Alerts that say "look at this".** Strong results (revenue ≥ 15% and PAT ≥ 20% YoY, a profit
turnaround, margin +200 bp on ≥ 10% growth, or ahead of commitments) are high importance — pushed,
titled "Strong …", ▲ in the inbox; a fall is ▼. DMA40 crosses are pushed like DMA200 crosses.

## Local model, government policy, company profiles (since 2026-11-04)

- **Local model** (`localllm.py`): `qwen3.8:27b` under ollama on this Mac (`brew services start ollama`), free, so its
  calls never count against `llm.daily_budget_inr` (logged in `llm_calls` as `local:<model>`, ₹0). It writes company
  profiles, matches policy and names the movers' triggers, and from 4 to 11 Nov 2026 also reads every live filing a
  second time (with one worked example) — its lines appear under gpt-4.1's on the event, so the two can be compared
  for a week (`llm.shadow_until`). About 0.5–5 minutes a call; one call per tick at most, most time-sensitive first.
- **Government policy** (`policy.py`): PIB's release list (all ministries) every 3 hours, 07:00–23:30. Releases from
  the Cabinet and the economic ministries, or whose title names a scheme, outlay, duty, PLI … are read, and the local
  model names the tracked companies the policy can move — positive / negative, how strongly, and through which channel
  (direct, supplier, customer, competitor) — as **Policy** events on those companies (high for a holding with a
  strong link). A release the ministry reposts is matched once. One release is matched per tick.
- **Company profiles** (`profiles.py`): what the company does, how its products are used and which markets its revenue
  depends on — directly and indirectly — with exposure tags; shown as "About the company" on the company page and fed
  to the policy matching. Five a day, least-invested holdings first, then the watchlist; sources are Screener's
  About / Key Points / sector chain plus the tracker's own concall notes, research reports and filing summaries;
  rewritten after 180 days.

## One card for many companies: policy, movers, technicals (since 2026-11-04)

- **Policy** — one card per PIB release: the companies it can move, a row each with impact, strength, channel and
  the reason, most affected first (holdings and direct links first among equals). Pushed only when a holding is hit
  directly, strongly and one way; a proposal or draft is at most "medium".
- **Movers** (`movers.py`) — after the session's prices: held and watched stocks that moved unusually *for them* —
  main board: ≥ 4% and ≥ 2σ of its own 60-session daily moves, or ≥ 3σ, or ≥ 2.5% on ≥ 3× its usual volume; SME: a
  move beyond its last 5 sessions on ≥ 2× their volume, or ≥ 3σ; plus DMA200 / golden / death crosses and phase
  changes. For each, the tracker's own events of the last 36 hours and the day's news headlines (Google News search)
  → the local model names the specific trigger (or "no specific trigger found") and an action (review / watch /
  none). One "Movers" card, at most 20 rows, reasons filled a few per tick. Thresholds under `movers:` in
  `tracker.yaml`.
- **Technicals** — the session's DMA crosses, golden / death crosses, phase changes, 52-week highs / lows and volume
  spikes are one "Technicals" card (a row per company, biggest first) instead of an alert each; each signal stays on
  its company's timeline at low importance. Pushed once, when a holding crossed DMA200 or had a golden / death cross.
- Each card also appears on the timeline of every company it lists.

**Order book between disclosures.** The order book is the last figure the company disclosed; the Order books page
now adds the dated orders won since (each counted once) as a running estimate — "≈ ₹1,706 Cr incl. +₹131.6 Cr
since" — and "Last updated" follows the latest of the two. An order filing counts as "filed again" only when its
orders' values match the earlier filing's; an order win's title carries its value and customer once read, and its
importance follows its size (≥ 5% of TTM revenue or ≥ ₹100 Cr → high).

## Missed updates, BSE-only stocks, daily Kite save (since 2026-11-02)

- **Catch-up.** The NSE RSS feeds keep only the latest items, so filings made while the laptop slept
  were lost. `feeds.catch_up` reads NSE's date-range APIs (announcements and integrated results
  filings, main board and SME, plus SAST Reg 29 stake changes) from the last sync to now — every 30
  minutes and on the first tick after a gap (up to 14 days back). Items are matched by attachment URL,
  so nothing is stored twice; a filing stored while its company wasn't tracked becomes an event once it
  is (e.g. added to the watchlist). Insider (PIT) and pledge filings still come only from the RSS.
- **BSE-only stocks** come through Screener in the dedicated Chrome profile (BSE's own API refuses
  automated clients, and the tracker doesn't disguise itself): checked every 45 minutes on the
  company's board-meeting day and the day after (the date its own intimation gives), every 3 hours in
  results season, else every 12 hours. A new period column in Screener's results table becomes a
  results event at once; the BSE PDFs are then read like NSE filings. The Chrome profile is started
  when it isn't running (your own Chrome is left alone). Caps: 300 page loads a day, 9 s apart, paused
  for the day at the first sign of a block.
- **Daily Kite save** for ZV3899, WN5759 and CQN676: weekdays 16:00, then every 30 minutes until
  21:00, until each account has saved today's holdings and written its daily report
  (`bash scripts/kite_daily_schedule.sh install|status|logs`). An account whose token expired gets the
  Kite login page opened in your browser — you sign in; nothing here sees your password. An account
  without `KITE_API_KEY_<ID>` / `KITE_API_SECRET_<ID>` in `.env` gets one notification a day.

**LLM budget.** `llm.daily_budget_inr` in `tracker.yaml` (now ₹10,000); the next tick reads it.

**Old transcripts.** `python -m tracker transcripts` shows how many are read; `--trash` moves the read
ones to the macOS Trash after asking (unread ones never move). Moved files are listed in
`transcripts/.archived.json`, so the concall fetcher doesn't download them again, and its own pruning
now moves files to the Trash — skipping any the tracker hasn't read — instead of deleting them.

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

**What a filing turns into (since 2026-10-20).** A tracked company's filing that goes to the LLM
first appears at low importance; after the read (usually within the same 3-minute tick) its event
carries a 3–5 line summary — numbers first — and its real importance, so the inbox gets one informative
alert instead of "Results filed".

- **Results:** one event per company per quarter, however many filings arrive (results PDF, press
  release, standalone and consolidated XBRL, newspaper extract). The headline uses the company's own
  numbers with YoY / QoQ; the summary covers margins, profit and anything unusual (one-offs, other-income
  or tax swings), and checks the quarter against what management committed to earlier — only commitments
  actually on record; with none, no "ahead / behind" verdict. EBITDA from a results PDF is computed from
  the printed P&L lines (PBT − exceptional items + finance cost + depreciation − other income, the same
  basis as the XBRL path), never the model's own figure; the YoY change in basis points comes from the
  year-ago quarter's printed lines. Summary lines that only say what a filing lacks ("no order book
  disclosed") are dropped. If the headline's quote isn't found word-for-word in the PDF, the event says so.
  NSE's XBRL figures are added as a labelled cross-check. A revision or correction gets its own event. The
  bare "Results filed" RSS item and XBRL copies of PDF announcements stay at low importance.
- **Concall transcripts and presentations:** summary on the event, plus a quarterly note (business,
  guidance with numbers, commitments and by when, triggers, tailwinds and policy, risks, tone) and a
  check of every open commitment (delivered / partly / missed / pending / changed).
- **Guidance events** list only guidance with numbers ("FY27 revenue growth 55–60%; FY27 EBITDA margin
  35–40%"); direction-only remarks stay in the summary.
- **Notes → Company notes:** one row per company; open it for the last four quarters (oldest → newest)
  and the latest view as the last column — what the company does, sector drivers and policies, the
  story and when it should play out, triggers with status, what to watch, delivery record, trend. For
  the past it is condensed once from the reports the earlier workflows wrote (only quarters those
  reports cover); afterwards each new transcript or results filing updates it. Also on the stock page
  under "Concall & outlook". Until every tracked company has its notes, each tick spends its LLM time on
  them before reading old PDFs (both share the back-fill cap); a company whose condensing call fails is
  retried on later days, three times at most.

**What the LLM is allowed to do.** Only `documents.py`/`extract.py` call it, only for new or
unread PDFs of tracked stocks, only on pages whose keywords matter, at most once per PDF (the
response is cached by file hash), plus small calls that condense notes, within `llm.daily_budget_inr`
(₹1,000 a day; back-fill of old PDFs and old reports capped at 60% of it, so new filings always have room).
Each call times out after `llm.request_timeout_s` (120 s, one retry), so a stalled request can't hold a
tick. Every call is logged and the Status page shows today's tokens and ₹ estimate. Every
extracted figure is checked against the PDF text: ✓ verbatim, ≈ approximate (near match, or
number and term on the same slide), ✗ not found — the last are kept but never plotted or scored.
An order-book figure whose quote never says "order book" (bids, inflow, revenue) is rejected.

**What the LLM reads (since 2026-11-04, `layout.py`).** The model never gets the PDF or its link — only page
text. Scanned pages (a page-sized image whose only text is the scanner's invisible OCR layer, or none) are OCR'd
again locally with tesseract at 300 dpi (`brew install tesseract`; ~2 s a page, no LLM cost); the scanner's layer
used to turn tables into column-by-column number lists with "I 8,899.76"-style misreads, and the model mixed up
quarters and the standalone and consolidated statements (AEROENTER Q2 FY27). Table pages are rebuilt one row per
line, every figure under its column (`Revenue from operations | 24,990.49 | 18,899.76 | 17,234.97 | …`).

**Number check (`numcheck.py`, no LLM).** Before a read is stored, every figure in its summary lines, headline,
anomalies and results numbers is looked up in the statement tables: the row that holds a ₹ figure gives its
year-ago and previous-quarter figures, so YoY / QoQ %, "7.7x", "from ₹z Cr" and up/down are recomputed; a figure
named for revenue, PAT, EPS, PBT or other income that no row holds is replaced by the statement's (consolidated
when the read is). The statement's arithmetic (revenue + other income = total income, …) and NSE's stored XBRL
quarters confirm the columns. A line whose figure the filing doesn't contain is dropped and the summary says
"check the PDF". A later read whose numbers were table-checked replaces an earlier one that wasn't.

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
python -m tracker bse            # due BSE-only companies via Screener (starts the Chrome debug profile)
python -m tracker valuations [--all] [--sid X] [--workers 4]   # valuation memos (stale ones, or every company)
python -m tracker kite-daily [--force]   # save today's holdings for every Kite account (the 16:00 job)
python -m tracker transcripts [--trash]  # old transcripts: read or not; move the read ones to the Trash
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
| `documents.py`, `extract.py` | filing PDFs → relevant pages → LLM extraction (summary, figures, results review, quarter note) with quote checks; insights back-fill |
| `layout.py`, `numcheck.py` | page text with tables as rows (local tesseract OCR for scanned pages); every AI figure checked and fixed against the statement |
| `localllm.py`, `policy.py`, `profiles.py` | the local model (ollama); PIB policy → companies it can move; company profiles (what it does, end-markets) |
| `movers.py` | the session's unusual moves among held and watched stocks, with the trigger (tracker events + news) |
| `results.py` | one results record and event per company per quarter (company numbers + NSE XBRL cross-check) |
| `company_notes.py` | quarterly notes, commitment checks, the company view; back-fill from earlier reports |
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
