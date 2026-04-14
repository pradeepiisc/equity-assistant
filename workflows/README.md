# Workflows — Investor Guide

Each workflow is a Python script that orchestrates one or more skills (data fetchers, LLM analysis, or rule-based engines) to produce an actionable output for you as an investor.

---

## How to Run Any Workflow

**Option A — Shell script (recommended, no Python knowledge needed):**
```bash
bash scripts/run_<workflow>.sh [args]
```

**Option B — Python directly:**
```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.<script_name> [args]
```

**Option C — Windsurf slash command** (type `/command` in the Windsurf chat panel).

---

## Daily Workflows (run every market day)

### `portfolio_daily_report.py`  ·  `scripts/run_daily_report.sh`
**What it does:** Generates a comprehensive 10-section portfolio health report every morning.

**What you get:**
1. **Overview** — Invested vs. current value, total P&L
2. **Position Sizing** — Alerts if any stock ≥5% of portfolio (you should reduce)
3. **DMA Status** — Which stocks are below 200 DMA (reduce signal) or >+10% above (avg-up candidate)
4. **Top 20 Holdings** — Your biggest positions with 50+200 DMA and vs-200% column
5. **Portfolio Concentration** — Cumulative table: how many stocks = 50% / 70% / 90% of your money
6. **SIP Signals** — 🟢 Entry / 🔵 Accumulate / 🟡 Hold / ⚠️ Overextended for high-conviction stocks
7. **Sector Concentration** — Which sectors dominate
8. **Top 10 Daily Movers** — What moved most today
9. **Tax Loss Harvest** — All stocks in loss, harvestable ₹ amount, ranked
10. **Watchlist Overlap** — Which watchlist stocks you already hold

```bash
bash scripts/run_daily_report.sh
bash scripts/run_daily_report.sh --no-dma   # faster, skips DMA fetch
```

Output: `portfolio/ZV3899/{today}/daily_report.md`

---

### `watchlist_alerts.py`  ·  `scripts/run_watchlist_alerts.sh`
**What it does:** For every stock in `watchlist.yaml`, fetches current price and DMA, then categorises as an entry signal.

**Entry signal categories:**
- 🟢 **Fresh Entry** — price below 200 DMA, strong SIP start
- 🔵 **Accumulate** — above 200 but below 50 DMA, stagger in
- 🟡 **Watch** — price near DMA, no urgency
- ⚠️ **Extended** — price >+20% above 200 DMA, wait for pullback
- ✅ **Already Held** — shows your current allocation + DMA position

```bash
bash scripts/run_watchlist_alerts.sh
```

Output: `portfolio/ZV3899/{today}/watchlist_alerts.md`

---

### `news_digest.py`  ·  `scripts/run_news_digest.sh`
**What it does:** Fetches recent headlines for your top 20 holdings + all watchlist stocks via Google News RSS (no API key needed).

```bash
bash scripts/run_news_digest.sh
bash scripts/run_news_digest.sh --top 30   # top 30 holdings instead of 20
```

Output: `portfolio/ZV3899/{today}/news_digest.md`

---

## Weekly Workflows

### `investor_activity.py`  ·  `scripts/run_investor_activity.sh`
**What it does:** Scrapes Screener.in shareholding tables for high-conviction holdings + watchlist stocks. Shows Promoter/FII/DII/Public stake across the last 4 quarters and flags changes ≥1.5pp QoQ.

```bash
bash scripts/run_investor_activity.sh                            # all >1% holdings + watchlist
bash scripts/run_investor_activity.sh --min-alloc 2.0           # only bigger positions
bash scripts/run_investor_activity.sh --symbols SYRMA AEROFLEX  # specific stocks
```

Output: `portfolio/ZV3899/{today}/investor_activity.md`

---

### `sector_analysis.py`  ·  `scripts/run_sector_analysis.sh`
**What it does:** Fetches LLM-enriched company profiles from Screener.in for all portfolio holdings.
Groups by **primary output industry** (where the company's product is directly sold — ≥5% revenue threshold).
Profiles are cached for 30 days. Prompt: `prompts/sector_profile.txt`.

**What you get:**
- Holdings grouped by primary output industry (no 2nd/3rd-degree overlap)
- Input + revenue cost sensitivity per company
- Per-company profile cards (Company Profiles section)

```bash
bash scripts/run_sector_analysis.sh                        # all holdings
bash scripts/run_sector_analysis.sh --symbols SYM1 SYM2   # specific stocks
bash scripts/run_sector_analysis.sh --refresh              # ignore cache, re-fetch all
bash scripts/run_sector_analysis.sh --no-llm               # scrape only, no LLM
```

Output: `portfolio/ZV3899/{snapshot_date}/sector_analysis.md`

---

### `watchlist_gap_analysis.py`  ·  `scripts/run_watchlist_gap_analysis.sh`
**What it does:** Cross-references your portfolio's sector coverage (from sector_analysis cache)
against all watchlist companies to find sectors **absent or thin** in your portfolio.
Recommends the **top 3 watchlist picks** that would add the most sector diversification.
Prompt: `prompts/watchlist_gap_analysis.txt`.

**Prerequisite:** Run `sector_analysis` first so profiles are fresh.

```bash
bash scripts/run_sector_analysis.sh          # ensure profiles are cached
bash scripts/run_watchlist_gap_analysis.sh   # find the gaps
```

Output: `portfolio/ZV3899/watchlist_gaps/{today}/watchlist_gap_analysis.md`

---

## Quarterly / Results Season Workflows

### `earnings_season.py`  ·  `scripts/run_earnings_season.sh`
**What it does:** All-in-one quarterly results season workflow. Fetches fresh shareholding data from Screener.in via Chrome CDP (real browser, auto-launches), compares with existing data, detects tracked investor movements, fetches new transcripts, and generates a comprehensive earnings season report.

**Key features:**
- **Shareholding delta detection** — actual QoQ changes (e.g., Promoter +2.6pp, FII -0.7pp)
- **Tracked investor alerts** — cross-references holders against `investor_watchlist.yaml`
- **DMA deployment map** — DMA40/DMA100 trend for all holdings
- **Sector allocation** — allocation % per sector with trend mix
- **Smart skip logic** — companies already at current quarter are skipped
- **Stop-on-block** — aborts after 5 consecutive failures; jittered 3-6s delays

```bash
bash scripts/run_earnings_season.sh                                    # report only (fast)
bash scripts/run_earnings_season.sh --fetch                           # fetch shareholding + transcripts (CDP)
bash scripts/run_earnings_season.sh --fetch --symbols QPOWER BETA     # specific stocks
bash scripts/run_earnings_season.sh --onboard "SYM:Company Name:URL"  # onboard inline
```

Output: `portfolio/ZV3899/holdings/{date}/earnings_season.md`

---

### `data_fetch.py`  ·  `scripts/run_data_fetch.sh`
**What it does:** Fetches all raw data for a single company in 4 steps:
1. **Concall PDFs** — downloads the last 4 quarters from Screener.in
2. **News articles** — fetches last 5 headlines via Google News RSS
3. **Shareholding pattern** — scrapes Promoter/FII/DII + individual holders from Screener.in
4. **Financial data** — scrapes P&L, Balance Sheet, Cash Flow, Key Ratios from Screener.in

Must be run before `full_company_analysis.py`.

```bash
bash scripts/run_data_fetch.sh DCAL
bash scripts/run_data_fetch.sh ZENTEC --skip-financials
bash scripts/run_data_fetch.sh QPOWER --refresh-financials
```

Output: `data/companies/{SYMBOL}/transcripts/`, `news/`, `shareholding/`, `financials/`

---

### `full_company_analysis.py`  ·  `scripts/run_full_analysis.sh`
**What it does:** Full deep-dive — runs 6 LLM skills in sequence, then synthesises into a master report:
1. `transcript_analysis` — management commentary, guidance, risks from concall PDFs
2. `shareholding_analysis` — promoter/FII/DII trend analysis
3. `news_sentiment` — sentiment and key events from recent news
4. `financial_snapshot` — P&L trends, margins, return ratios from financial data
5. `peer_comparison` — competitive positioning vs listed peers
6. `valuation_agent` — **sector-specific forward fair value** (uses all of the above as context)

**Requires:** Azure OpenAI credentials in `.env`. Company must be in `watchlist.yaml`.
**Requires:** `data_fetch.py` run first.

```bash
bash scripts/run_full_analysis.sh QPOWER
bash scripts/run_full_analysis.sh DCAL
```

Output: `data/companies/{SYMBOL}/reports/master_report.md` + individual skill reports (including `valuation_agent.md`)

---

### `skills/valuation_agent.py`  ·  `scripts/run_valuation.sh`  *(standalone)*
**What it does:** Sector-aware forward-looking intrinsic valuation of a single company. Can be run standalone without a full analysis pipeline.

**How it works:**
1. Reads `data/sector_profiles/{SYMBOL}.json` to detect the company's sector
2. Loads the matching expert prompt from `prompts/sector_valuation/{sector}.txt` (27 sectors covered)
3. Pulls context from: master_report.md + raw financials + any previously run skill outputs
4. Calls LLM → produces fair value range, 3 scenarios (Bull/Base/Bear), and entry/exit triggers
5. Saves `valuation_agent.md` + `valuation_agent.json` to reports folder

**Output fields:**
- `fair_value_low` / `fair_value_high` — intrinsic value range (₹)
- `entry_signal` — Buy Now / Accumulate on Dips / Hold / Trim / Exit
- `sector_kpis` — sector-specific operational metrics (ARPOB for hospitals, spread for chemicals, etc.)
- `scenarios` — Bull / Base / Bear with probability and fair value per scenario
- `entry_exit_triggers` — specific conditions that should change your position

**Requires:** Company in `watchlist.yaml`. Sector profile helps (run `sector_analysis` first); falls back to `generic` prompt if missing.

```bash
bash scripts/run_valuation.sh TARIL
bash scripts/run_valuation.sh YASHO
bash scripts/run_valuation.sh ZENTEC
```

Output: `data/companies/{SYMBOL}/reports/valuation_agent.md`

---

### `batch_company_analysis.py`  ·  `scripts/run_batch_analysis.sh`
**What it does:** Runs `data_fetch` + `full_company_analysis` for **every company in `watchlist.yaml`**.
Use `--skip-existing` to only process companies that don't yet have a `master_report.md`.

```bash
bash scripts/run_batch_analysis.sh --skip-existing     # only new/missing reports
bash scripts/run_batch_analysis.sh                     # re-run all (overwrites)
bash scripts/run_batch_analysis.sh --data-only         # data fetch only, no LLM
bash scripts/run_batch_analysis.sh --analysis-only     # LLM only (data already fetched)
bash scripts/run_batch_analysis.sh --symbols ZENTEC TARIL  # specific companies
```

Output: `data/companies/{SYMBOL}/reports/master_report.md` for each company.
Runtime: ~5–10 min per company. Full 23-company batch ≈ 2–4 hours.

---

### `onboard_company.py`  *(new company — one command)*
**What it does:** Full onboarding pipeline for a brand-new watchlist company in one command:
1. Adds entry to `watchlist.yaml`
2. Creates `data/companies/{SYMBOL}/` folder structure
3. Fetches all raw data via `data_fetch` (concalls, news, shareholding, financials, insights)
4. Extracts insight metric values via `extract_insights_values` (LLM table from PDFs)
5. Fetches ValuePickr forum expert analysis
6. Runs `full_company_analysis` → valuation + master report

**Chrome CDP auto-detected:** If Chrome is not running, shareholding/financials/insights are auto-skipped with clear instructions to complete those later. Concalls and news are always fetched (no CDP needed).

```bash
python -m workflows.onboard_company ROSSTECH \
    --name "Rossell Techsys Ltd" \
    --sector "Aerospace and Defence" \
    --screener-url https://www.screener.in/company/ROSSTECH/ \
    --bse-code 544294 \
    --reason "Make in India; Aerospace and Defence tailwinds"

# After Chrome is launched, fetch the CDP-dependent data:
bash scripts/launch_chrome_debug.sh   # Terminal.app, NOT Windsurf
python -m workflows.data_fetch ROSSTECH
```

Output: `data/companies/{SYMBOL}/reports/master_report.md` + `valuepickr.md`

---

### `extract_insights_values.py`
**What it does:** Reads concall transcripts + PPTs for a company (or all companies) and uses LLM to extract a structured quarterly table of **Screener Insights metric values** — e.g., order book, ARPOB, spread, utilisation, guidance figures quoted in PDFs.

This is a separate step from `data_fetch` because it's LLM-powered and can take 2–3 min per company.

```bash
python -m workflows.extract_insights_values --symbols ROSSTECH EIEL
python -m workflows.extract_insights_values          # all watchlist + portfolio companies
python -m workflows.extract_insights_values --refresh  # re-run even if already done
```

Output: `data/companies/{SYMBOL}/insights/insights_values.md`

---

### `growth_ranking.py`
**What it does:** Runs `growth_signals` skill (LLM) for every company in watchlist + portfolio, assigns a **growth score 1–10 and trajectory label** (Accelerating / Stable / Decelerating / Weak), then ranks all companies.

**Used for:** Identifying your highest-conviction companies by actual growth trajectory, not just price. Re-run quarterly after results season.

```bash
python -m workflows.growth_ranking                      # all companies
python -m workflows.growth_ranking --symbols ROSSTECH   # specific companies
python -m workflows.growth_ranking --refresh            # force re-analysis
```

Output: `portfolio/ZV3899/growth_ranking_{date}.md`

---

### `top_holdings_valuation.py`
**What it does:** Runs `valuation_agent` for your **top N holdings by portfolio weight**. Produces fair value ranges (₹) and entry signals for each. Useful for sizing decisions — which of your largest holdings are still cheap vs. fair vs. expensive?

```bash
python -m workflows.top_holdings_valuation              # top 10 by value
python -m workflows.top_holdings_valuation --top 20     # top 20
python -m workflows.top_holdings_valuation --symbols DCAL ZENTEC  # specific
```

Output: `data/companies/{SYMBOL}/reports/valuation_agent.md` for each

---

### `conviction_builder.py`
**What it does:** For a set of stocks (e.g., today's top losers), synthesises DMA position + holding data + all available analysis into an **Add / Hold / Trim / Exit recommendation** with price levels.

**Not a sequence step** — run on-demand when you want to review specific positions, typically daily losers or high-allocation stocks.

```bash
python -m workflows.conviction_builder --top-losers 5    # today's 5 biggest losers
python -m workflows.conviction_builder --symbols DCAL ZENTEC  # specific stocks
```

Output: `portfolio/ZV3899/{today}/conviction_review.md`

---

### `skills/valuepickr_fetcher.py`  *(standalone skill)*
**What it does:** Searches the [ValuePickr forum](https://forum.valuepickr.com) for expert community discussion on a company. Finds the best matching thread, fetches the original thesis post + early discussion + recent posts, saves as markdown.

**No auth required.** Uses the Discourse public JSON API.

```bash
python -m skills.valuepickr_fetcher EIEL --name "Enviro Infra Engineers"
python -m skills.valuepickr_fetcher ROSSTECH --name "Rossell Techsys"
```

Output: `data/companies/{SYMBOL}/valuepickr.md`

---

## Workflow Independence — Important

The analysis workflows are **all independent** — none of them must be run as a sequence except for the explicit prerequisite of `data_fetch` before `full_company_analysis`:

| Workflow | Prerequisite | Frequency |
|---|---|---|
| `data_fetch` | None (but Chrome CDP needed for financials/shareholding/insights) | Per company, before analysis |
| `full_company_analysis` | `data_fetch` run first | Per company, quarterly |
| `batch_company_analysis` | Same as above, for all watchlist | Quarterly / results season |
| `extract_insights_values` | Transcripts/PPTs in `data/companies/` | Quarterly |
| `growth_ranking` | Transcripts in `data/companies/` | Quarterly |
| `top_holdings_valuation` | Transcripts + financials | Quarterly / ad-hoc |
| `conviction_builder` | Analysis reports optional (degrades gracefully) | On-demand (daily losers) |
| `earnings_season` | Chrome CDP for `--fetch` (auto-launches); DMA for report | Quarterly / results season |
| `sector_analysis` | None (scrapes live from Screener) | Weekly |
| `portfolio_daily_report` | Today's Kite snapshot | Daily |
| `watchlist_alerts` | None | Daily |

---

## Windsurf Slash Commands

Type `/command` in the Windsurf chat panel to get step-by-step guidance with run buttons.

| Slash command | Shell script equivalent | What it does |
|---|---|---|
| `/daily_run` | `run_daily_report.sh` | Full morning: Kite login → portfolio → report → alerts → news |
| `/fetch_portfolio` | *(Kite login only)* | Kite login + portfolio save, no report |
| `/portfolio_daily_report` | `run_daily_report.sh` | Daily report only (needs saved portfolio) |
| `/watchlist_alerts` | `run_watchlist_alerts.sh` | DMA entry signals for watchlist stocks |
| `/news_digest` | `run_news_digest.sh` | Google News headlines for holdings + watchlist |
| `/investor_activity` | `run_investor_activity.sh` | Shareholding tracker (weekly) |
| `/sector_analysis` | `run_sector_analysis.sh` | LLM sector profiles, output industry grouping |
| `/batch_company_analysis` | `run_batch_analysis.sh` | Batch data fetch + analysis for all watchlist |
| `/watchlist_gap_analysis` | `run_watchlist_gap_analysis.sh` | Top 3 watchlist picks for missing sectors |
| `/chrome_debug` | `launch_chrome_debug.sh` | Launch Chrome with CDP debug port 9222 |

---

## Cron Job Examples

```bash
# Every weekday at 8:30am — full morning run
30 8 * * 1-5 bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_daily_report.sh

# Every weekday at 8:35am — watchlist DMA alerts
35 8 * * 1-5 bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_watchlist_alerts.sh

# Every Sunday at 9am — weekly investor activity + sector analysis
0 9 * * 0 bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_investor_activity.sh
5 9 * * 0 bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_sector_analysis.sh

# First Sunday of every quarter — batch company analysis
# (set manually or use a cron job manager)
```

---

## Workflow Decision Guide

| Situation | Run this |
|---|---|
| Every morning before trading | `bash scripts/run_daily_report.sh` |
| Market is down — check if watchlist stocks are at entry | `bash scripts/run_watchlist_alerts.sh` |
| Want company news quickly | `bash scripts/run_news_digest.sh` |
| Results season — check if FIIs are entering/exiting | `bash scripts/run_investor_activity.sh` |
| Weekly sector review | `bash scripts/run_sector_analysis.sh` |
| Which watchlist stocks diversify the portfolio most? | `bash scripts/run_watchlist_gap_analysis.sh` |
| **Just discovered a company, want everything in one shot** | `python -m workflows.onboard_company SYM --name "..." --sector "..."` |
| Deep research on one company (already in watchlist) | `bash scripts/run_data_fetch.sh SYM` → `bash scripts/run_full_analysis.sh SYM` |
| Extract metric values from transcripts (LLM) | `python -m workflows.extract_insights_values --symbols SYM` |
| Which companies are growing fastest right now? | `python -m workflows.growth_ranking` |
| Are your biggest holdings cheap or expensive? | `python -m workflows.top_holdings_valuation` |
| Today's losers — should I add or exit? | `python -m workflows.conviction_builder --top-losers 5` |
| Results season — shareholding deltas + tracked investors | `bash scripts/run_earnings_season.sh --fetch` |
| Results season — update all watchlist companies | `bash scripts/run_batch_analysis.sh --skip-existing` |
| Community sentiment on a stock | `python -m skills.valuepickr_fetcher SYM --name "Full Name"` |

---

## Relationship Between Workflows and Skills

```
Skills (atomic functions)            Workflows (orchestrators)
────────────────────────             ──────────────────────────
concall_fetcher.py        ──────►   data_fetch.py  (Step 1)
news_fetcher.py           ──────►   data_fetch.py  (Step 2)
shareholding_fetcher.py   ──────►   data_fetch.py  (Step 3)
financial_fetcher.py      ──────►   data_fetch.py  (Step 4)
                                         │
transcript_analysis.py    ──────►   full_company_analysis.py  (LLM)
shareholding_analysis.py  ──────►   full_company_analysis.py  (LLM)
news_sentiment.py         ──────►   full_company_analysis.py  (LLM)
financial_snapshot.py     ──────►   full_company_analysis.py  (LLM)
peer_comparison.py        ──────►   full_company_analysis.py  (LLM)
valuation_agent.py ◄──── (all above results as context)
valuation_agent.py        ──────►   full_company_analysis.py  (LLM Step 6)
                                         │
data_fetch.py             ──────►   batch_company_analysis.py
full_company_analysis.py  ──────►   batch_company_analysis.py
                                         │
dma_fetcher.py            ──────►   portfolio_daily_report.py (no LLM)
                          ──────►   watchlist_alerts.py        (no LLM)

news_fetcher.py           ──────►   news_digest.py             (no LLM)

sector_analysis.py cache  ──────►   watchlist_gap_analysis.py  (LLM)
master_report.json        ──────►   watchlist_gap_analysis.py  (LLM)

(Screener scraping direct)──────►   investor_activity.py       (no LLM)

valuation_agent.py        ──────►   standalone  (scripts/run_valuation.sh)
```

---

## Prompts

All LLM prompts are stored in `prompts/` as plain-text files so they can be tuned without touching Python code.

| Prompt file | Used by | Purpose |
|---|---|---|
| `sector_profile.txt` | `sector_analysis.py` | Extract primary output industry (≥5% revenue rule) |
| `watchlist_gap_analysis.txt` | `watchlist_gap_analysis.py` | Find sector gaps, rank top 3 diversification picks |
| `transcript_analysis.txt` | `skills/transcript_analysis.py` | Concall management commentary analysis |
| `shareholding_analysis.txt` | `skills/shareholding_analysis.py` | FII/DII/promoter trend analysis |
| `news_sentiment.txt` | `skills/news_sentiment.py` | News sentiment and key events |
| `financial_snapshot.txt` | `skills/financial_snapshot.py` | P&L, margins, return ratio analysis |
| `master_report.txt` | `workflows/full_company_analysis.py` | Synthesis → rating + thesis + fair value summary |
| `peer_comparison.txt` | `skills/peer_comparison.py` | Competitive positioning |
| `sector_valuation/generic.txt` | `skills/valuation_agent.py` | Generic multi-method valuation (fallback) |
| `sector_valuation/hospital.txt` | `skills/valuation_agent.py` | Hospital chains — EV/EBITDA, ARPOB, bed pipeline |
| `sector_valuation/electrical_equipment.txt` | `skills/valuation_agent.py` | Transformers, switchgear, HVDC — order book P/E |
| `sector_valuation/defence.txt` | `skills/valuation_agent.py` | Defence electronics — govt order book P/E |
| `sector_valuation/ems.txt` | `skills/valuation_agent.py` | EMS/PCB — order book, ODM mix, PLI |
| `sector_valuation/pharma.txt` | `skills/valuation_agent.py` | Pharma — ANDA pipeline, US generic recovery |
| `sector_valuation/chemicals.txt` | `skills/valuation_agent.py` | Chemicals — spread, capacity utilisation, EV/EBITDA |
| `sector_valuation/nbfc.txt` | `skills/valuation_agent.py` | NBFC/gold loans — AUM, NIM, P/B |
| `sector_valuation/banking.txt` | `skills/valuation_agent.py` | Banks — loan book, NIM, GNPA, P/B |
| `sector_valuation/infra.txt` | `skills/valuation_agent.py` | EPC/infra — order book, WC, P/E |
| *(+ 18 more sector prompts)* | `skills/valuation_agent.py` | pharma_services, fintech, logistics, metal_recycling, textiles, consumer_durables, gems_jewellery, hospitality, entertainment, aquaculture, animal_feeds, agri_commodities, ev, battery_components, aerospace, power_energy, steel, industrial_systems |
