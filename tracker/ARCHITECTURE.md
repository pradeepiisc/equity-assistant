# Stock tracker — architecture

How the tracker turns exchange files into the dashboard, and where each model is used. Setup on a new machine:
[SETUP.md](SETUP.md). What each page shows: [README.md](README.md).

```
                ┌──────────────────────────── scheduler (jobs.py · tick every 3 min) ────────────────────────────┐
                │  dashboard server (server.py) ticks while open; the LaunchAgent ticks while it is closed;      │
                │  a lock file lets one tick run at a time, and each job runs only when it is due                │
                └────────────────────────────────────────────────────────────────────────────────────────────────┘
   sources                         ingest                              read and check                     out
 ─────────────            ─────────────────────────           ───────────────────────────────         ─────────────
 NSE RSS + date-range  →  feeds.py, classify.py       →  events ─────────────────────────────────→  inbox, alerts
 APIs (filings, SAST)     company match, filing type                                                  (alerts.py,
                                                                                                      macOS push)
 filing PDFs (NSE, BSE) → documents.py: download,     →  layout.py: page text                     →  results_reviews,
                          dedupe by SHA-256              · scanned page → tesseract OCR (300 dpi)     order book, orders,
                                                         · tables → one row per line, cells in        guidance, notes,
                                                           their columns                              events with a
                                                         extract.py: gpt-4.1 (Azure, JSON) reads      3–5 line summary
                                                         the relevant pages → numcheck.py checks
                                                         every figure against the statement table
                                                         (fixes it, or drops the line)
 NSE results XBRL      →  financials.py               →  financials_q (exact quarterly figures)
 NSE / BSE bhavcopy    →  prices.py, corpactions.py   →  technicals.py: DMAs, phases, crosses     →  one "Technicals"
                                                                                                      card a session
                                                         movers.py: unusual moves (own σ, volume) →  one "Movers" card
                                                         + tracker events + Google News headlines     with the trigger
                                                         → local model names the trigger              per stock
 PIB releases          →  policy.py (every 3 h)       →  local model: which tracked companies     →  one "Policy" card
                                                         a policy moves, and how                       per release
 Screener pages,       →  profiles.py (5 a day)       →  local model: what the company does,      →  "About the
 concall notes,                                          its end-markets and exposures                 company" card
 research reports
 Kite holdings,        →  universe.py                 →  stocks (held, watched, in notes)
 watchlist.yaml
                                     all of it in data/tracker/tracker.db (SQLite) + raw files in data/tracker/
```

## Models

| Task | Model | Where | Cost |
|---|---|---|---|
| Reading filings (results, presentations, transcripts, orders): summary, figures, results review, quarter notes, company view, valuation memo | gpt-4.1 | Azure OpenAI through `llm/client.py` (config `llm.client`) | within `llm.daily_budget_inr` |
| A second read of every live filing, 4–11 Nov 2026 (`extract.shadow_read`): same pages and prompt plus one worked example, same number check; shown under gpt-4.1's on the event | qwen3.8:27b | local | free |
| OCR of scanned pages | tesseract 5 (`eng`) | local binary | free |
| Number check of every AI figure | none — `numcheck.py` (rules) | local | free |
| Company profiles, policy matching, movers' triggers | qwen3.8:27b (27.8B, nvfp4, 18 GB) | ollama on this Mac, `localllm.py` | free |

The comparison behind the split (2026-11-04, the ten latest filings, judged blind against the PDFs): with one worked
example the local model matched or slightly beat gpt-4.1 — 4 vs 5 wrong figures, usefulness 2.8 vs 2.7, readability
4.3 vs 4.1, clarity 3.9 vs 3.7, fewer misleading lines — and covered one checklist item fewer; without the example it
left out expenses and margins more often. It takes 0.5–5 minutes a filing on an M4 Pro (gpt-4.1: 2–20 seconds): a
27B model reads all 18 GB of its weights for every token it writes, and this Mac's memory moves about 273 GB/s.
So for one week both read every filing — gpt-4.1's read drives the alert, the local read sits under it — and then
one is chosen (`llm.shadow_until`). The local model does one task a tick, most time-sensitive first: the movers'
reasons, the second read of the newest filing, a PIB release's companies, a company profile.

## Reading a filing (documents.py → layout.py → extract.py → numcheck.py)

1. **discover** a tracked stock's filing (NSE announcement, or a BSE PDF via Screener for BSE-only stocks) →
   `documents` row; **download** it (deduplicated by URL and content hash).
2. **page text** (`layout.py`, cached as `data/tracker/text/<sha>.l2.json`): a page that is mostly one image with only
   an invisible OCR layer (or none) is a scan → rendered at 300 dpi and OCR'd with tesseract; the scanner's own layer
   is ignored. The word boxes are grouped into lines (after straightening a tilted scan), and in a table every figure
   is placed under its column (columns found from the figures' right edges), so a missing cell stays empty:
   `Revenue from operations | 24,990.49 | 18,899.76 | 17,234.97 | …`. Digital pages with many figures get the same
   treatment from their character boxes; other pages keep their text.
3. **prepare**: keyword scoring per page; pages worth reading.
4. **read** (`extract.py`): gpt-4.1 gets the pages and returns JSON (summary, order book, orders, guidance, capex,
   results, quarter note). Responses are cached by content hash + prompt version.
5. **check** (`numcheck.py`): the statement tables are parsed (unit per page — lakh, million, crore; consolidated or
   standalone from the title; column roles from the header dates or period labels, else the SEBI order; the
   statement's arithmetic confirms OCR); every ₹ figure, %, multiple, bps and EPS in the summary, headline and
   anomalies is located in its row and column — YoY / QoQ / margins recomputed, a figure in the wrong unit or from the
   other statement replaced, a line whose figure the filing doesn't contain dropped ("check the PDF").
6. **store** (`extract.persist`): figures with their quotes, results review (one per company per quarter — a later
   read whose numbers were table-checked replaces an earlier one), events.

`python -m tracker reread ID …` reads documents again through all of it.

## Storage

`data/tracker/tracker.db` (git-ignored): stocks, holdings, prices, technicals, feed_items, events, alerts, documents,
llm_calls, results_reviews, financials_q, order_book_history, orders, guidance, doc_facts, company_notes,
company_view, valuations, company_profiles, policy_releases, kv. Raw exchange files, PDFs, page-text and LLM caches
sit beside it under `data/tracker/` and `data/companies/<folder>/filings/`.

## Cards that list many companies

Policy releases, the session's movers and the session's technical signals are one event each (`sid` empty, the rows
in `data.table`), rendered as a table by `web/ui.js` (`EvTable`) and shown on each listed company's timeline too.
