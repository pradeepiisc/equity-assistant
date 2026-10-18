# Tracker — Phase 2 Plan

Written 2026-10-18 at the end of the phase-1 session. The **prompt to paste into a new
session** is at the bottom.

## Status — phase 2 built (2026-10-18, branch `tracker-phase2`)

Decisions taken at the start: order dashboard = **tracked stocks only**; LLM =
**gpt-4.1 (`client_gpt41_itam`), 500k tokens/day**, back-fill ≤ 60% of it; announcements
polled **every 3 min** on weekdays 09:00–18:00; **LaunchAgent installed** (every 3 min).

| Part | State |
|---|---|
| P0 scheduling | Done. Tick lock, per-feed cadence, `serve` polls at start-up + every 3 min, Refresh-now forces every feed, Status page with both pollers, tick history, LaunchAgent install/uninstall. |
| P1 named holders | Done. `shareholding_holders` + `holder_coverage`; NSE SHP XBRL **does** name every >1% public holder (and all promoters), so NSE stocks use it; Screener for history and BSE-only. Anchor entry/exit/≥0.5 pp events; tracked-investor events moved here (same dedupe keys). |
| P2 documents + LLM | Done. NSE filings + 799 on-disk PDFs (since 2023) → text (PDFium) → keyword pages → gpt-4.1 → order book / orders / guidance (with scope) / capex / macro, each quote checked against the PDF. Results XBRL parsed without LLM. `insights_values.json` excerpts re-located in their PDFs (no LLM). |
| P4 dashboard | Done. Order books (+ history overlay), Orders (by company / feed), order-book card on the stock page, AI banner everywhere. |
| P5 estimates + score | Done. Next-12-month revenue (guidance → order book → trend), forward EPS / P/E, 11-component score with reasons, Priorities page, stock "Score & estimates" tab. |
| P3 BSE-only fallback | Built, **not yet exercised live** (no Chrome debug session during the build). BSE API → 403 for plain clients; BSE PDFs download fine. Cap, spacing, due rules and stop-on-block tested offline. |

Open follow-ups: run the BSE fallback once with the Chrome debug session up; the first 21
documents were read with prompt v1 (orders/guidance scope rules came later — re-reading them
costs ~₹50); Kite websocket prices remain optional.

## Where things stand (phase 1, in `tracker/`)

| Area | State |
|---|---|
| Exchange connection | Yes. Pulls straight from NSE/BSE over HTTPS: bhavcopy prices (NSE + BSE, incl. SME), NSE corporate-action file (splits/bonuses), 9 NSE RSS filing feeds, NSE bulk/block deal CSVs. No Screener, no browser. |
| Golden/death cross, DMA40/200 crosses, phases | Computed locally in `tracker/technicals.py` from the stored, split-adjusted closes: a cross fires when DMA50 vs DMA200 (or close vs DMA40/200) flips between the previous and the latest session, with a 0.5% confirmation band. |
| Scheduler | `scripts/tracker_schedule.sh install` registers a macOS LaunchAgent that runs `python -m tracker tick` every 10 min. **Not installed** — nothing polls today. |
| Dashboard server | `python -m tracker serve` only serves pages. It does **not** poll; it shows whatever the last `tick` stored. |
| Data location | `data/tracker/tracker.db` (SQLite) + raw exchange files under `data/tracker/raw/` (git-ignored). |
| LLM use | **None** in the tracker. `tick` costs zero tokens. The stock page only *reads* reports the old workflows generated with Azure OpenAI (`llm/client.py`). |
| Shareholding | Reads Screener `.txt` files fetched by `workflows/data_fetch.py` (Chrome/CDP session). `tracker/shareholding.py` already parses the `### Public Anchor Investors`, `### DIIs (Individual)`, `### Promoters (Individual)` sections, but only uses them to match names — they are never stored or shown. |
| Order book / guidance | Shown only from existing LLM reports (`reports/*.json`, `insights_values.json`). Nothing refreshes them when a company files. Order-win filings are detected with a regex ₹ value. |
| Notes | `notes/*.md` with `$SYMBOL add/reduce …` — manual. |

## What the user wants (from the 2026-10-18 message + screenshots)

1. **Always-fresh data without babysitting.** Pull on server start, keep polling while the
   server runs, keep polling when it doesn't (LaunchAgent), and react as fast as the exchanges
   allow. NSE/BSE offer no public push/webhook for filings, so "callback" = frequent polite
   polling of the RSS feeds plus an event pipeline that fires immediately on a new item.
2. **Know what costs money.** Keep the polling path LLM-free; spend tokens only on new
   documents that matter, cached, with a daily cap.
3. **Automatic document ingestion from NSE/BSE, not Screener.** When a tracked company files
   results, an investor presentation, a concall transcript, an order win or a press release,
   download the PDF, extract order book, order inflow, revenue/margin/EPS guidance, capex and
   expansion, sector tailwinds/headwinds, govt policy and interest-rate commentary, and fold
   it into the metrics (order-book history, forward revenue/EPS).
4. **BSE-only stocks (27)** still need documents. Use Screener only as a fallback, gently
   enough not to get blocked.
5. **Add / reduce suggestions computed by the tool** from the company's own data (order
   growth, book-to-revenue, guidance vs delivery, margins, EPS growth, valuation, technical
   phase, promoter/institution/anchor flows). Notes become optional, not required.
6. **Anchor investors** (already in the Screener shareholding files) shown on the stock page
   with quarter-over-quarter changes, refreshed whenever new shareholding data arrives.
7. **Order-book dashboard like the screenshots**:
   - *Orderbook view*: filters (3M/6M/1Y, min order book ₹ Cr, min market cap ₹ Cr);
     "Top gainers — last 1Y" card strip (rank, % growth, name, NSE code, mini bar history,
     latest book); table — Company (NSE/BSE codes), Growth (1Y), Order book, Revenue (FY,
     consolidated), Book/Revenue, Market cap, As-of date, Last updated, Order-book history
     sparkline with latest value and % change; sortable, searchable, paginated.
   - *Order-book history overlay* (click a sparkline): quarterly bars Q4FY18…Q1FY27 with
     value labels, latest book, as-of, data points, 3M/6M/1Y growth, the quoted sentence
     "From the filing", and a source-PDF link per bar.
   - *Company view*: per company over a timeframe (e.g. 6 months) — Orders as % of revenue,
     order count, total order value, company revenue (FY, standalone/consolidated), market
     cap; expandable rows listing individual orders: date, customer, order type, contract
     value, duration, annual value, revenue %, PDF link.
   - *Order view*: chronological feed of all individual orders.
   - Banner: "AI-extracted, may contain errors — verify against the source PDF."
   - Result: a priority list of stocks by expected growth in actual numbers.

## Plan

### P0 — Scheduling (no LLM)
- Single-instance lock (`data/tracker/tick.lock`) so server loop and LaunchAgent never run
  `tick` concurrently.
- `serve` runs one `tick` at startup (background thread, UI loads immediately) and then a
  loop every N minutes while running; a "Refresh now" button calls `/api/t/run/tick`.
- Faster cadence for the announcements feed during 09:00–18:00 (configurable, default 3 min;
  other feeds stay at 10 min). Process new items immediately → classify → alert.
- Keep the LaunchAgent for when the server is off; document install/uninstall/status in the UI
  (status page shows "background polling: on/off", last tick, next tick).
- Optional later: Kite Connect websocket (`integrations/kite_connect.py`) for intraday prices;
  needs a daily login token, so not default.

### P1 — Anchor and named holders (no LLM)
- New table `shareholding_holders(sid, qdate, grp, name, pct, source)`; persist every
  `### …` section `shareholding.parse_file()` already parses.
- Stock page Shareholding tab: "Anchor investors", "DIIs", "Promoters" tables with quarter
  columns and change arrows; new/exited holders flagged; events for anchor entry/exit/≥0.5 pp.
- Verify whether NSE's shareholding-pattern filing (XBRL attached to the SHP RSS item) gives
  named >1% public holders; if yes, use it for NSE stocks and keep Screener for BSE-only.

### P2 — Document pipeline from NSE (LLM only here)
- For tracked stocks, on new RSS items of type results / investor presentation / transcript
  / order win / press release / capacity / acquisition: download the attachment PDF to
  `data/companies/<folder>/filings/` (dedupe by URL + SHA-256).
- Results without LLM: parse the NSE financial-results XBRL (revenue, EBITDA, PAT, EPS) when
  attached; fall back to PDF tables.
- LLM extraction (Azure OpenAI via `llm/client.py`) into a JSON schema with a verbatim quote
  and page number per fact: `order_book_cr`, `as_of`, `order_inflow_cr`, individual orders
  (customer, type, value, duration), revenue/volume/margin/EPS guidance, capex & capacity
  timelines, tailwinds, headwinds, govt policy, interest-rate sensitivity.
- Tables: `documents`, `doc_facts`, `orders`, `order_book_history`, `guidance`.
- Back-fill once from PDFs already on disk under `data/companies/*/` and from
  `insights_values.json`, so order-book history starts populated.
- Cost controls: only tracked stocks; regex/keyword pre-filter; cache by PDF hash (never
  re-extract); text-only, trimmed to relevant pages; cheaper model for "is this relevant",
  stronger model for extraction; daily token budget in `tracker.yaml`; usage logged per call
  and shown on the status page.

### P3 — BSE-only fallback via Screener (gentle)
- Reuse the existing logged-in Chrome/CDP session (`skills/cdp_helper.py`) — no new scraping
  tricks. At most 1 request / 8–10 s, ≤ 30 companies per day, spread across the day.
- Only fetch a company when something is expected: board-meeting/result date within ±7 days,
  quarter-end shareholding window, or not refreshed in 30 days.
- Stop for the day on the first 403/429/captcha and alert; never retry in a loop.
- First check whether BSE's own public pages/RSS are reachable without a browser; prefer them.

### P4 — Order-book dashboard (screenshots)
- New pages: **Order books**, **Orders**, **Company orders**, plus the history overlay, built
  in the existing Preact UI (`tracker/web/`) and the existing palette/dark-mode tokens.
- Growth = latest order book vs the value ≥ 3/6/12 months earlier; Book/Revenue uses TTM or
  latest FY revenue (label which).
- Scope: tracked universe by default; a toggle for "all NSE companies with order filings"
  (discovery), using regex-classified order wins and LLM only above a size threshold.

### P5 — Forward estimates and Add/Reduce score (no LLM in the scoring)
- Forward revenue = executable order book / execution period + guidance; forward EPS from
  guided margin and share count; show guidance-vs-delivery hit rate.
- Transparent rule-based score per stock with listed reasons: order-book growth, book/revenue,
  guidance delivery, margin trend, EPS growth vs P/E, technical phase, promoter / FII / DII /
  anchor flows, pledges. Output: Add / Hold / Trim / Exit / Watch, weights in `tracker.yaml`.
- Notes remain an optional override. This is decision support, not an order.

## Decisions to confirm at the start of the new session
1. Order dashboard scope: tracked universe only, or also market-wide discovery?
2. Daily LLM budget (tokens or ₹) and which Azure config to use.
3. Faster announcements polling during market hours: 3 min OK?
4. Install the LaunchAgent now?

---

## Prompt for the new session

```
Repo: ~/Documents/learning/equity-assistant. Read TRACKER_FUTURE_PLAN.md and tracker/README.md
first — phase 1 (tracker/ package: NSE/BSE bhavcopy, NSE RSS filings, bulk/block deals,
technicals, alerts, Preact dashboard on :7788) is built and working. Don't rewrite it; extend it.

Goal for this session: implement phase 2 from TRACKER_FUTURE_PLAN.md in this order —
P0 scheduling, P1 anchor/named holders, P2 NSE document pipeline with LLM extraction,
P4 order-book dashboard (Orderbook view, history overlay, Company orders, Orders feed —
modelled on the screenshots described in the plan), then P5 forward estimates + Add/Reduce
score. P3 (Screener fallback for BSE-only stocks) last.

Constraints:
- Polling path stays LLM-free. LLM only for new/unseen PDFs of tracked stocks, cached by hash,
  with a daily budget from tracker.yaml and usage shown on the status page. Use llm/client.py.
- Use NSE/BSE as the data source. Screener only for BSE-only stocks, via the existing CDP
  session, rate-limited as the plan says; stop on the first block.
- Every extracted figure keeps its verbatim quote, page and source-PDF link.
- Ask me the 4 decisions listed in the plan before starting P2/P4.
- Verify in the browser pane (light + dark, phone width) before saying a page is done.
```
