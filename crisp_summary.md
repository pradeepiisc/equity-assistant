# Equity Assistant — Architecture Crisp Summary

> **Purpose:** Single-page reference for all skills, workflows, scripts, their data sources,
> outputs, and where each output is consumed downstream.
>
> **Last updated:** 2026-05-05

---

## 1. Raw Data Layer (no LLM — fetchers write to disk)

| Fetcher | Reads From | Output Path | Requires CDP? | Usage (consumed by) |
|---|---|---|---|---|
| `concall_fetcher` | Screener.in concall listing | `transcripts/*.pdf`, `ppt/*.pdf` | Yes (CDP for page listing; httpx for PDF downloads) | `transcript_analysis`, `growth_signals`, `insights_values` |
| `financial_fetcher` | Screener.in financials page | `financials/*.txt` | Yes (CDP primary) | `financial_snapshot`, `peer_comparison`, `valuation_agent`, `quant_valuation_ranking` |
| `shareholding_fetcher` | Screener.in shareholding page | `shareholding/*.txt` | Yes (CDP primary) | `shareholding_analysis`, `quant_valuation_ranking`, `earnings_season` |
| `insights_fetcher` | Screener.in Insights Beta section | `insights/*_insights_*.txt` | Yes + Screener login for values | `growth_signals` (raw labels/values as context) |
| `news_fetcher` | Google News RSS | `news/*.json` | No | `news_sentiment` |
| `valuepickr_fetcher` | ValuePickr forum thread (httpx scrape) | `valuepickr.md` | No | `valuepickr_analysis` |
| `dma_fetcher` | yfinance → Screener.in → Tickertape (multi-strategy) | `portfolio/.cache/dma_{date}.json` | No | `portfolio_daily_report`, `watchlist_alerts`, `conviction_builder`, `earnings_season`, `anchor_momentum_rotation` |

---

## 2. LLM Skills (consume raw data → `reports/*.{md,json}`)

| Skill | Reads From | Output | Usage (consumed by) |
|---|---|---|---|
| `transcript_analysis` | `transcripts/*.pdf` + `ppt/*.pdf` | `reports/transcript_analysis.{md,json}` | `full_company_analysis` → `master_report`; `conviction_builder` (reads JSON) |
| `financial_snapshot` | `financials/*.txt` | `reports/financial_snapshot.{md,json}` | `full_company_analysis` → `master_report`; `conviction_builder` (reads JSON); `valuation_agent` (via skill_results) |
| `shareholding_analysis` | `shareholding/*.txt` | `reports/shareholding_analysis.{md,json}` | `full_company_analysis` → `master_report`; `conviction_builder` (reads JSON) |
| `news_sentiment` | `news/*.json` | `reports/news_sentiment.{md,json}` | `full_company_analysis` → `master_report` |
| `peer_comparison` | `financials/*.txt` of company + peers listed in YAML | `reports/peer_comparison.{md,json}` | `full_company_analysis` → `master_report` |
| `growth_signals` | `transcripts/*.pdf` + `ppt/*.pdf` + `insights/*.txt` | `reports/growth_signals.{md,json}` | `growth_ranking` (portfolio report); `valuation_agent._gather_context()` (reads JSON); `conviction_builder` (reads JSON) |
| `insights_values` | `transcripts/*.pdf` + `ppt/*.pdf` | `insights/insights_values.md` | `full_company_analysis` → `master_report`; `valuation_agent._gather_context()` (reads file directly) |
| `valuepickr_analysis` | `valuepickr.md` | `reports/valuepickr_analysis.{md,json}` | `full_company_analysis` → `master_report` (community peer-review: sentiment, headwinds, promoter scrutiny, missed signals) |
| `valuation_agent` | `sector_profiles/{sym}.json` + `insights/insights_values.md` + `financials/*.txt` + `reports/master_report.md` (if exists) + skill_results (transcript, financial, shareholding, news) | `reports/valuation_agent.{md,json}` | `full_company_analysis` → `master_report`; `conviction_builder` (reads JSON); `top_holdings_valuation` |

> **Note on growth_signals vs transcript_analysis:** Both read transcripts + PPTs from the same
> folder but serve different purposes. `transcript_analysis` extracts management commentary,
> guidance tone, and red flags. `growth_signals` focuses on bottom-line growth trajectory
> and assigns a growth bucket (HIGH / MEDIUM / LOW) with a 1–10 score.

---

## 3. Non-LLM Skills / Utilities

| Skill | What It Does | Output | Usage |
|---|---|---|---|
| `company_meta` | Stores/retrieves authoritative company name, sector, screener_url; fetches latest price from holdings or yfinance; scrapes Screener for name/sector via CDP | `data/companies/{sym}/meta.json` | Used by all skills/workflows for consistent naming |
| `dma_fetcher` | Multi-strategy DMA retrieval (yfinance → Screener.in → Tickertape) for 40/99/50/200 DMA | `portfolio/.cache/dma_{date}.json` | `portfolio_daily_report`, `watchlist_alerts`, `conviction_builder`, `earnings_season` |
| `dma_chart` | Generates matplotlib price chart with 40 DMA (yellow) and 99 DMA (black) lines | `portfolio/{user}/charts/{sym}.png` | Visual inspection; `portfolio_daily_report` top 10 charts |
| `cdp_helper` | Chrome DevTools Protocol helper — page navigation, HTML extraction, screenshot, Screener login | N/A (library) | All CDP-dependent fetchers |

---

## 4. Workflows (orchestrate skills and fetchers)

### 4a. Per-Company Workflows

| Workflow | Skills / Fetchers It Runs | Key Inputs | Output | Usage |
|---|---|---|---|---|
| `data_fetch` | concall_fetcher + financial_fetcher + shareholding_fetcher + insights_fetcher + news_fetcher + valuepickr_fetcher | Company symbol | All raw data under `data/companies/{sym}/` | Prerequisite for all LLM skills |
| `extract_insights_values` | `insights_values` skill | Existing transcripts + PPTs | `insights/insights_values.md` per company | `full_company_analysis` → `valuation_agent` |
| `full_company_analysis` | transcript_analysis + shareholding_analysis + news_sentiment + financial_snapshot + peer_comparison + valuepickr_analysis + valuation_agent → master_report | All company data | `reports/master_report.{md,json}`, `reports/valuation_agent.{md,json}`, + 5 skill reports | `conviction_builder`; human reading |
| `onboard_company` | watchlist.yaml entry + folder creation + meta.json + `data_fetch` + `extract_insights_values` + valuepickr_fetcher + `full_company_analysis` + `sector_analysis` | Company symbol + metadata | All reports for new company + YAML entry + sector profile | One-time company onboarding |
| `quarterly_review` | concall_fetcher + full_company_analysis | Company symbol (or all watchlist) | Fresh transcripts + updated reports | Results-season refresh |

### 4b. Portfolio-Wide Workflows

| Workflow | Skills / Fetchers It Runs | Key Inputs | Output | Usage |
|---|---|---|---|---|
| `batch_company_analysis` | `data_fetch` + `full_company_analysis` for all/selected companies | watchlist.yaml or --symbols | All reports for all companies | Batch refresh |
| `growth_ranking` | `growth_signals` skill for all companies | Transcripts + PPTs + insights | `reports/growth_signals.{md,json}` per company + `portfolio/{user}/growth_ranking_{date}.md` | Growth leaderboard |
| `conviction_builder` | **No skills run** — reads cached JSON reports | growth_signals.json, financial_snapshot.json, transcript_analysis.json, valuation_agent.json, shareholding_analysis.json, DMA cache, holdings | `portfolio/{user}/holdings/{date}/conviction_review.md` | Add / Hold / Trim / Exit decisions |
| `sector_analysis` | Screener.in HTTP scrape + inline LLM | Screener.in company page | `data/sector_profiles/{sym}.json` + `portfolio/{user}/sector/{date}/sector_analysis.md` | Cross-sector exposure map; `valuation_agent` reads profiles |
| `top_holdings_valuation` | `valuation_agent` for top N holdings by value | Portfolio holdings + sector_profiles + financials | `portfolio/{user}/holdings/{date}/top_holdings_valuation.md` + ranking | Expert LLM valuation for top holdings |
| `watchlist_alerts` | **No LLM** — pure DMA math | DMA cache + watchlist.yaml | `portfolio/{user}/{date}/watchlist_alerts.md` | Entry signals (buy / wait / avoid) |
| `watchlist_gap_analysis` | LLM synthesis | master_report.md + sector_profiles | `portfolio/{user}/{date}/watchlist_gap_analysis.md` | Tiered ranking: Act Now / Monitor / Watch / Remove |
| `watchlist_monitor` | `full_company_analysis` for every watchlist company | watchlist.yaml | Updated reports for all watchlist stocks | Full watchlist analysis sweep |
| `ensure_company_data` | Batch folder creation + optional data fetch for all portfolio+watchlist | Both YAMLs | Folder structures + raw data | Housekeeping / gap-filling |
| `investor_activity` | Screener.in CDP scrape | watchlist.yaml | Shareholding change report | Promoter / FII trend monitoring |
| `earnings_season` | shareholding CDP scrape + concall_fetcher + DMA | Portfolio holdings + tracked investors | `portfolio/{user}/holdings/{date}/earnings_season.md` | Quarterly shareholding deltas, tracked investor alerts |

### 4c. Daily / News Workflows

| Workflow | What It Does | Output | LLM? |
|---|---|---|---|
| `portfolio_daily_report` | Daily health: P&L, sizing alerts, DMA status, concentration, top movers, charts | `portfolio/{user}/daily_report.md` | No |
| `news_digest` | Google News RSS headlines for top 20 holdings + watchlist | `portfolio/{user}/{date}/news_digest.md` | No |
| `news_material_events` | Filters news_digest for material events via keyword rules | `portfolio/{user}/{date}/material_events.md` | No |
| `consolidated_exit_strategy` | Consolidates all master_report.json decision frameworks | `portfolio/consolidated_exit_strategy.md` | No |
| `anchor_momentum_rotation` | DMA40/90-based trend classification for momentum rotation | Trend buckets for each holding | No |

### 4d. Scripts (standalone, no workflow wrapper)

| Script | What It Does | Output |
|---|---|---|
| `scripts/generate_valuation_report.py` | Pure data-driven 5-dimension scoring (Growth/Profitability/Valuation/Balance Sheet/Shareholding) for ALL companies | `data/valuation/master_ranking.{md,json}` + `data/companies/{sym}/reports/valuation_summary.md` |
| `scripts/launch_chrome_debug.sh` | Start Chrome in remote debugging mode (port 9222) | Chrome process (prerequisite for CDP) |
| `scripts/consolidate_holdings.py` | Consolidate multiple portfolio user holdings | Merged holdings report |
| `scripts/exchange_kite_token.py` | Exchange Zerodha Kite access token | `.env` token update |

---

## 5. Overlaps (actual vs. intentional)

| Overlap | Where | Verdict |
|---|---|---|
| `growth_signals` and `transcript_analysis` both read transcripts + PPTs | Two separate skills in `full_company_analysis` + `growth_ranking` | **Intentional, different purpose.** transcript_analysis → management commentary + tone + guidance. growth_signals → growth trajectory score + bucket. Separate LLM calls, different prompts. |
| `valuation_agent` internally re-runs `growth_signals` context if `growth_signals.json` not found on disk | `valuation_agent._gather_context()` calls `_growth_signals_context()` | **Intentional fallback.** If `growth_ranking` ran first, it reads cached JSON. If not, it re-derives inline. No double work when run via `full_company_analysis`. |
| `conviction_builder` reads the same JSON reports that `full_company_analysis` generates | Disk reads — no re-computation | **Not an overlap.** conviction_builder is a consumer, not a producer. It just assembles existing outputs into a hold/add/trim verdict. |
| `sector_analysis` produces sector_profiles, `valuation_agent` reads them | Two separate steps | **Sequential dependency, not overlap.** Run sector_analysis first (weekly), valuation_agent uses the cached profile. |
| `quant_valuation_ranking` (script) vs `valuation_agent` (LLM skill) | Script is pure math; skill is LLM-based | **Complementary.** Script gives fast quantitative scores; LLM agent gives deep qualitative analysis. Both contribute to investment decisions. |

---

## 6. Data Flow Diagram

```
════════════════════════════════════════════════════════════════════════════════════
  INPUT SOURCES          FETCHERS (raw)     LLM SKILLS              WORKFLOWS / USE CASES
════════════════════════════════════════════════════════════════════════════════════

                         concall_fetcher
Screener.in ────────────►  (transcripts/   ──┬──► transcript_analysis  ──┐
  concall page              ppt/ PDFs)        ├──► growth_signals  ────── │──► growth_ranking.md
                                              └──► insights_values  ───── │
                                                                           │
Screener.in ────────────► financial_fetcher  ──► financial_snapshot ──── │
  financials page           (financials/)       ► quant_valuation_ranking │
                                                                           │
Screener.in ────────────► shareholding_fetcher ► shareholding_analysis ──┤
  shareholding page         (shareholding/)     ► quant_valuation_ranking │
                                                                           ▼
Screener.in ────────────► insights_fetcher     ► growth_signals (extra) ──► full_company_analysis
  Insights Beta             (insights/ labels)                             │
                                                                           │  runs all skills above
Google News RSS ────────► news_fetcher  ──────► news_sentiment ──────── │  + valuepickr_analysis
                           (news/ JSON)                                    │  + valuation_agent
                                                                           │        │
ValuePickr forum ───────► valuepickr_fetcher ► valuepickr_analysis ──── │        │
                           (valuepickr.md)                                 │        │
                                                                           │        │
Screener.in ────────────► sector_analysis ───► sector_profiles/{sym}.json │       │
  (HTTP, no CDP)           (weekly, cached)      └──────────────────────── │ ───► valuation_agent
                                                                           │        │
                                                                           ▼        ▼
                                                                    master_report.md
                                                                    valuation_agent.md
                                                                    growth_signals.md
                                                                    transcript_analysis.md
                                                                    shareholding_analysis.md
                                                                    financial_snapshot.md
                                                                    news_sentiment.md
                                                                    valuepickr_analysis.md
                                                                    valuation_summary.md (quant)
                                                                           │
                                        yfinance DMA ──────────────────── │
                                        Portfolio holdings ────────────────┤
                                                                           ▼
                                                                  conviction_builder
                                                                  (reads all JSONs above)
                                                                           │
                                                                  ┌────────┴─────────┐
                                                                  ▼                  ▼
                                                         conviction_review.md   top_holdings_valuation.md
                                                         (Add/Hold/Trim/Exit)   (LLM valuation ranking)

════════════════════════════════════════════════════════════════════════════════════
  STANDALONE OUTPUTS (no LLM or DMA-only)
  watchlist_alerts.md         ←  DMA cache + watchlist.yaml  (entry signals)
  sector_analysis.md          ←  sector_profiles/ + portfolio (sector exposure map)
  growth_ranking.md           ←  growth_signals for all      (growth leaderboard)
  earnings_season.md          ←  CDP shareholding + concall_fetcher + DMA
  portfolio_daily_report.md   ←  holdings + DMA + charts     (daily health)
  news_digest.md              ←  Google News RSS              (headlines)
  material_events.md          ←  news_digest + keyword filter (alerts)
  watchlist_gap_analysis.md   ←  master_report + sector_profiles (tiered ranking)
  master_ranking.md           ←  financials + shareholding    (quant scoring)
  consolidated_exit_strategy  ←  all master_report.json       (exit dashboard)
════════════════════════════════════════════════════════════════════════════════════
```

---

## 7. End-User Flow — Short Version

```
Step 1 (once per company):   python -m workflows.onboard_company SYMBOL --name "..." --sector "..."
     — or manual steps:
       python -m workflows.data_fetch SYMBOL
       python -m workflows.extract_insights_values --symbols SYMBOL
       python -m workflows.full_company_analysis SYMBOL

Weekly cadence:
  python -m workflows.sector_analysis              # sector grouping (30-day cache)
  python -m workflows.growth_ranking               # growth leaderboard
  python -m workflows.conviction_builder           # Add / Hold / Trim / Exit per holding
  python -m workflows.watchlist_alerts             # DMA entry signals for watchlist
  python -m workflows.watchlist_gap_analysis       # watchlist tiered ranking

Daily cadence:
  python -m workflows.portfolio_daily_report       # daily health report
  python -m workflows.news_material_events         # material news alerts

Quarterly cadence:
  python -m workflows.earnings_season              # shareholding + results deltas
  python -m workflows.quarterly_review             # fresh transcripts + analysis
  python scripts/generate_valuation_report.py      # quant scoring for all companies
```

---

## 8. End-User Flow — Detailed Version

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ONBOARD A NEW COMPANY                                                   ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  python -m workflows.onboard_company SYMBOL \                            ║
║      --name "Company Name" --sector "Sector" \                           ║
║      --screener-url https://www.screener.in/company/SYMBOL/ \            ║
║      --bse-code 123456 --reason "Investment thesis"                      ║
║                                                                          ║
║  Does automatically:                                                     ║
║    ① Add to watchlist.yaml + create folder structure + meta.json        ║
║    ② data_fetch — transcripts, news, shareholding, financials, insights ║
║    ③ extract_insights_values — LLM quarterly metric table from PDFs      ║
║    ④ valuepickr_fetcher — community forum thread                        ║
║    ⑤ sector_analysis — sector profile for valuation context             ║
║    ⑥ full_company_analysis — all 8 skill reports + master_report        ║
║                                                                          ║
║  Output: data/companies/{SYMBOL}/reports/master_report.md  ← read first ║
║          data/companies/{SYMBOL}/reports/valuation_agent.md ← for price ║
║          data/sector_profiles/{SYMBOL}.json  ← sector context            ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  REFRESH AN EXISTING COMPANY (new concall quarter released)              ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.data_fetch SYMBOL          # fetch new concalls  ║
║  ② python -m workflows.extract_insights_values \                         ║
║         --symbols SYMBOL --refresh                # refresh metric table ║
║  ③ python -m workflows.full_company_analysis SYMBOL  # fresh reports    ║
║                                                                          ║
║  Or quick: python -m workflows.quarterly_review SYMBOL                   ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  DAILY MONITORING                                                        ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.portfolio_daily_report                            ║
║       → P&L, sizing alerts, DMA status, concentration, top movers       ║
║       → Generates DMA charts for top 10 holdings                        ║
║       → Output: portfolio/{user}/daily_report.md                        ║
║                                                                          ║
║  ② python -m workflows.news_material_events                              ║
║       → Fetches RSS headlines + filters for material events             ║
║       → Output: portfolio/{user}/{date}/material_events.md              ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  WEEKLY PORTFOLIO REVIEW (run in this order)                             ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.sector_analysis                                   ║
║       → Groups all holdings by output industry (themes, cross-sector)   ║
║       → Cached 30 days; only re-fetches stale company profiles           ║
║                                                                          ║
║  ② python -m workflows.growth_ranking                                    ║
║       → Scores every portfolio company HIGH / MEDIUM / LOW growth        ║
║                                                                          ║
║  ③ python -m workflows.conviction_builder                                ║
║       → Reads all cached JSON reports + DMA + P&L                        ║
║       → Outputs Add / Hold / Trim / Exit per holding                    ║
║                                                                          ║
║  ④ python -m workflows.watchlist_alerts                                  ║
║       → Pure DMA math — flags watchlist stocks at entry zones           ║
║                                                                          ║
║  ⑤ python -m workflows.watchlist_gap_analysis                            ║
║       → Scores watchlist on thesis + sector coverage gap                ║
║       → Tiered ranking: Act Now / Monitor / Watch / Remove              ║
║                                                                          ║
║  ⑥ python -m workflows.top_holdings_valuation                            ║
║       → LLM valuation for top N holdings (50% coverage by default)      ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  QUARTERLY PORTFOLIO REVIEW                                              ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.earnings_season                                   ║
║       → Shareholding deltas, tracked investor alerts, new transcripts   ║
║                                                                          ║
║  ② python -m workflows.quarterly_review                                  ║
║       → Fresh concalls + full analysis for all companies                ║
║                                                                          ║
║  ③ python scripts/generate_valuation_report.py                           ║
║       → Pure quant scoring for all companies (no LLM)                   ║
║       → Output: data/valuation/master_ranking.md                        ║
║                                                                          ║
║  ④ python -m workflows.consolidated_exit_strategy                        ║
║       → Consolidates all decision frameworks into exit dashboard        ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  READING ORDER — once reports are generated                              ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  1. master_report.md       ← Big picture: thesis, risks, catalysts      ║
║  2. valuation_agent.md     ← Fair value range, entry signal, scenarios  ║
║  3. valuepickr_analysis.md ← Community view: what we might have missed  ║
║  4. transcript_analysis.md ← Deep dive: management commentary           ║
║  5. growth_signals.md      ← Growth trajectory score and drivers        ║
║  6. valuation_summary.md   ← Quantitative scoring (5-dimension)         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 9. Prompts Reference

| Prompt File | Used By | Purpose |
|---|---|---|
| `transcript_analysis.txt` | `transcript_analysis` skill | Management commentary extraction (tone, guidance, red flags, business problems) |
| `financial_snapshot.txt` | `financial_snapshot` skill | Financial health summary from Screener data |
| `shareholding_analysis.txt` | `shareholding_analysis` skill | Promoter/FII/DII pattern analysis |
| `news_sentiment.txt` | `news_sentiment` skill | News article sentiment classification |
| `peer_comparison.txt` | `peer_comparison` skill | Comparative financial analysis vs peers |
| `growth_signals.txt` | `growth_signals` skill | Growth trajectory scoring (HIGH/MEDIUM/LOW) |
| `insights_values.txt` | `insights_values` skill | Quarterly metric table extraction from PDFs |
| `master_report.txt` | `full_company_analysis` workflow | Final synthesis of all skill outputs into master report |
| `conviction_review.txt` | `conviction_builder` workflow | Add/Hold/Trim/Exit recommendation per holding |
| `sector_profile.txt` | `sector_analysis` workflow | Output-industry classification |
| `watchlist_gap_analysis.txt` | `watchlist_gap_analysis` workflow | Watchlist tiered ranking synthesis |
| `sector_valuation/*.txt` | `valuation_agent` skill | Sector-specific valuation methodology (30+ sectors) |

---

## 10. Configuration Files

| File | Purpose |
|---|---|
| `config.yaml` | LLM provider, model, paths, skill settings |
| `watchlist.yaml` | Stocks being monitored (not held) — symbol, name, sector, screener_url, bse_code, watch_reason, peers |
| `portfolio_companies.yaml` | Stocks held in portfolio — similar fields plus screener_symbol |
| `investor_watchlist.yaml` | Tracked investors for smart-money detection in shareholding |
| `sector_map.yaml` | Manual sector → industry mapping overrides |
| `.env` | API keys (Zerodha Kite, Azure OpenAI) — gitignored |

---

*Generated: auto-maintained — update when skills or workflows change.*
