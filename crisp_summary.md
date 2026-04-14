# Equity Assistant — Architecture Crisp Summary

> **Purpose:** Single-page reference for all skills, workflows, their data sources,
> outputs, and where each output is consumed downstream.

## 1. Raw Data Layer (no LLM — fetchers write to disk)

| Fetcher | Reads From | Output Path | Requires CDP? | Usage (consumed by) |
|---|---|---|---|---|
| `concall_fetcher` | Screener.in concall listing | `transcripts/*.pdf`, `ppt/*.pdf` | Yes (CDP for page listing; httpx for PDF downloads) | `transcript_analysis`, `growth_signals`, `insights_values` |
| `financial_fetcher` | Screener.in financials page | `financials/*.txt` | Yes (CDP primary) | `financial_snapshot`, `peer_comparison`, `valuation_agent._gather_context()` |
| `shareholding_fetcher` | Screener.in shareholding page | `shareholding/*.txt` | Yes (CDP primary) | `shareholding_analysis` |
| `insights_fetcher` | Screener.in Insights Beta section | `insights/*_insights_*.txt` | Yes + Screener login for values | `growth_signals` (raw labels/values as context) |
| `news_fetcher` | Google News RSS | `news/*.json` | No | `news_sentiment` |
| `valuepickr_fetcher` | ValuePickr forum thread (httpx scrape) | `valuepickr.md` | No | `valuepickr_analysis` |

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
| `valuepickr_analysis` *(new)* | `valuepickr.md` | `reports/valuepickr_analysis.{md,json}` | `full_company_analysis` → `master_report` (community peer-review: sentiment, headwinds, promoter scrutiny, missed signals) |
| `valuation_agent` | `sector_profiles/{sym}.json` + `insights/insights_values.md` + `financials/*.txt` + `reports/master_report.md` (if exists) + skill_results (transcript, financial, shareholding, news) | `reports/valuation_agent.{md,json}` | `full_company_analysis` → `master_report`; `conviction_builder` (reads JSON) |

> **Note on growth_signals vs transcript_analysis:** Both read transcripts + PPTs from the same
> folder but serve different purposes. `transcript_analysis` extracts management commentary,
> guidance tone, and red flags. `growth_signals` focuses on bottom-line growth trajectory
> and assigns a growth bucket (HIGH / MEDIUM / LOW) with a 1–10 score.

---

## 3. Workflows (orchestrate skills and fetchers)

| Workflow | Skills / Fetchers It Runs | Key Inputs | Output | Usage (consumed by / read by) |
|---|---|---|---|---|
| `data_fetch` | concall_fetcher + financial_fetcher + shareholding_fetcher + insights_fetcher + news_fetcher + valuepickr_fetcher | Company symbol | All raw data under `data/companies/{sym}/` | Prerequisite for all LLM skills |
| `extract_insights_values` | `insights_values` skill | Existing transcripts + PPTs | `insights/insights_values.md` per company | `full_company_analysis` → `valuation_agent` |
| `full_company_analysis` | transcript_analysis + shareholding_analysis + news_sentiment + financial_snapshot + peer_comparison + valuepickr_analysis + valuation_agent → master_report | All company data | `reports/master_report.{md,json}`, `reports/valuation_agent.{md,json}`, + 5 skill reports | `conviction_builder` (reads individual JSONs); human reading |
| `growth_ranking` | `growth_signals` skill for all companies | Transcripts + PPTs + insights | `reports/growth_signals.{md,json}` per company + `portfolio/ZV3899/growth_ranking_{date}.md` | `conviction_builder` (reads growth_signals.json); human reading |
| `conviction_builder` | **No skills run** — reads cached JSON reports | `reports/growth_signals.json`, `financial_snapshot.json`, `transcript_analysis.json`, `valuation_agent.json`, `shareholding_analysis.json`, DMA cache, portfolio holdings | `portfolio/ZV3899/holdings/{date}/conviction_review.md` | Human reading — Add / Hold / Trim / Exit decisions |
| `sector_analysis` | Screener.in HTTP scrape + inline LLM | Screener.in company page | `data/sector_profiles/{sym}.json` + `portfolio/ZV3899/sector/{date}/sector_analysis.md` | `valuation_agent._gather_context()` (reads sector profile JSON); human reading |
| `watchlist_alerts` | **No LLM** — pure DMA math | DMA cache + portfolio holdings | `portfolio/ZV3899/{date}/watchlist_alerts.md` | Human reading — entry signal (buy / wait / avoid) |
| `batch_company_analysis` | `data_fetch` + `full_company_analysis` for all watchlist companies | watchlist.yaml | All reports for all watchlist companies | Batch onboarding / refresh |
| `onboard_company` | `data_fetch` + `full_company_analysis` | Company symbol + metadata | All reports for new company + YAML entry | One-time company onboarding |
| `investor_activity` | Screener.in CDP scrape | watchlist.yaml | Shareholding change report | Human reading — promoter / FII trend monitoring |
| `earnings_season` | shareholding CDP scrape + concall_fetcher + DMA | Portfolio holdings + tracked investors | `portfolio/ZV3899/holdings/{date}/earnings_season.md` | Human reading — quarterly shareholding deltas, tracked investor alerts, new transcripts/results, DMA deployment map |

---

## 4. Overlaps (actual vs. intentional)

| Overlap | Where | Verdict |
|---|---|---|
| `growth_signals` and `transcript_analysis` both read transcripts + PPTs | Two separate skills in `full_company_analysis` + `growth_ranking` | **Intentional, different purpose.** transcript_analysis → management commentary + tone + guidance. growth_signals → growth trajectory score + bucket. Separate LLM calls, different prompts. |
| `valuation_agent` internally re-runs `growth_signals` context if `growth_signals.json` not found on disk | `valuation_agent._gather_context()` calls `_growth_signals_context()` | **Intentional fallback.** If `growth_ranking` ran first, it reads cached JSON. If not, it re-derives inline. No double work when run via `full_company_analysis`. |
| `conviction_builder` reads the same JSON reports that `full_company_analysis` generates | Disk reads — no re-computation | **Not an overlap.** conviction_builder is a consumer, not a producer. It just assembles existing outputs into a hold/add/trim verdict. |
| `sector_analysis` produces sector_profiles, `valuation_agent` reads them | Two separate steps | **Sequential dependency, not overlap.** Run sector_analysis first (weekly), valuation_agent uses the cached profile. |

---

## 5. Data Flow Diagram

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
  financials page           (financials/)                                  │
                                                                           │
Screener.in ────────────► shareholding_fetcher ► shareholding_analysis ──┤
  shareholding page         (shareholding/)                                │
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
                                                                           │
                                        yfinance DMA ──────────────────── │
                                        Portfolio holdings ────────────────┤
                                                                           ▼
                                                                  conviction_builder
                                                                  (reads all JSONs above)
                                                                           │
                                                                           ▼
                                                                  conviction_review.md
                                                                  (Add / Hold / Trim / Exit)

════════════════════════════════════════════════════════════════════════════════════
  STANDALONE OUTPUTS (no LLM or DMA-only)
  watchlist_alerts.md  ←  DMA cache + watchlist.yaml   (entry signals, no LLM)
  sector_analysis.md   ←  sector_profiles/ + portfolio  (cross-sector exposure map)
  growth_ranking.md    ←  growth_signals for all        (portfolio growth leaderboard)
  earnings_season.md   ←  CDP shareholding + concall_fetcher + DMA (quarterly delta report)
════════════════════════════════════════════════════════════════════════════════════
```

---

## 6. End-User Flow — Short Version

```
Step 1 (once per company):   python -m workflows.data_fetch SYMBOL
Step 2 (once per company):   python -m workflows.extract_insights_values --symbols SYMBOL
Step 3 (once per company):   python -m workflows.full_company_analysis SYMBOL

Weekly cadence:
  python -m workflows.sector_analysis        # output-industry grouping (30-day cache)
  python -m workflows.growth_ranking         # portfolio growth leaderboard
  python -m workflows.conviction_builder     # Add / Hold / Trim / Exit per holding
  python -m workflows.watchlist_alerts       # DMA entry signals for watchlist
```

---

## 7. End-User Flow — Detailed Version

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ONBOARD A NEW COMPANY                                                   ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  python -m workflows.onboard_company \                                   ║
║      --symbol SYMBOL --name "Company Name" --sector "Sector"             ║
║                                                                          ║
║  Does automatically:                                                     ║
║    ① data_fetch — transcripts, news, shareholding, financials, insights ║
║    ② extract_insights_values — LLM quarterly metric table from PDFs      ║
║    ③ full_company_analysis — all 8 skill reports + master_report         ║
║                                                                          ║
║  Output: data/companies/{SYMBOL}/reports/master_report.md  ← read first ║
║          data/companies/{SYMBOL}/reports/valuation_agent.md ← for price ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  REFRESH AN EXISTING COMPANY (new concall quarter released)              ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.data_fetch SYMBOL          # fetch new concalls  ║
║                                                    # auto-launches Chrome ║
║  ② python -m workflows.extract_insights_values \                         ║
║         --symbols SYMBOL --refresh                # refresh metric table ║
║  ③ python -m workflows.full_company_analysis SYMBOL  # fresh reports    ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════╗
║  WEEKLY PORTFOLIO REVIEW (run in this order)                             ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ① python -m workflows.sector_analysis                                   ║
║       → Groups all holdings by output industry (themes, cross-sector)   ║
║       → Cached 30 days; only re-fetches stale company profiles           ║
║       → Output: portfolio/ZV3899/sector/{date}/sector_analysis.md       ║
║                                                                          ║
║  ② python -m workflows.growth_ranking                                    ║
║       → Scores every portfolio company HIGH / MEDIUM / LOW growth        ║
║       → Reads transcripts + PPTs + Screener insights for each            ║
║       → Output: portfolio/ZV3899/growth_ranking_{date}.md               ║
║                                                                          ║
║  ③ python -m workflows.conviction_builder                                ║
║       → Reads all cached JSON reports + DMA + P&L                        ║
║       → Outputs Add / Hold / Trim / Exit per holding with conviction %   ║
║       → Output: portfolio/ZV3899/holdings/{date}/conviction_review.md   ║
║                                                                          ║
║  ④ python -m workflows.watchlist_alerts                                  ║
║       → Pure DMA math — no LLM — fast                                   ║
║       → Flags watchlist stocks at DMA entry zones                        ║
║       → Output: portfolio/ZV3899/{date}/watchlist_alerts.md             ║
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
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

*Generated: auto-maintained — update when skills or workflows change.*
