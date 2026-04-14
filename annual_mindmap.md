# Equity Assistant — Annual Mindmap

*Your complete 1-year schedule for data-driven portfolio management.*

---

## Daily (every market day)

| Time | Task | Script | What you get |
|---|---|---|---|
| **09:00** | Morning News Digest | `bash scripts/run_news_digest.sh` | Headlines for top holdings + watchlist (Google News RSS) |
| **15:30** | Portfolio Daily Report | `bash scripts/run_daily_report.sh` | 10-section dashboard: P&L, DMA status, sizing alerts, movers, tax harvest, SIP signals |
| **15:35** | Watchlist DMA Alerts | `bash scripts/run_watchlist_alerts.sh` | 🟢 Entry / 🔵 Accumulate / ⚠️ Extended signals for all watchlist stocks |

**Cron setup (add via `crontab -e`):**
```bash
0  9 * * 1-5  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_news_digest.sh
30 15 * * 1-5  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_daily_report.sh
35 15 * * 1-5  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_watchlist_alerts.sh
```

---

## Weekly (every Sunday)

| Task | Script | What you get |
|---|---|---|
| **Investor Activity** | `bash scripts/run_investor_activity.sh` | Promoter/FII/DII stake changes ≥1.5pp QoQ — flags smart money moving in/out |
| **Sector Analysis** | `bash scripts/run_sector_analysis.sh` | Sector coverage map for all holdings (profiles cached 30 days — re-fetches only when stale) |
| **Watchlist Gap Analysis** | `bash scripts/run_watchlist_gap_analysis.sh` | Top 3 watchlist picks that fill missing sector gaps in the portfolio |

**Cron setup:**
```bash
0  9 * * 0  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_investor_activity.sh
5  9 * * 0  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_sector_analysis.sh
10 9 * * 0  bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/run_watchlist_gap_analysis.sh
```

---

## Monthly (first Sunday of each month)

| Task | How | What to do |
|---|---|---|
| **Portfolio sizing review** | Daily report | Check if any position crossed 5% threshold. Trim if needed. |
| **Watchlist refresh** | Edit `watchlist.yaml` | Add/remove companies. If >25 stocks, prune low-conviction ones. |
| **Sector gap re-check** | `/watchlist_gap_analysis` | Re-run after any new purchases to see if gaps have closed. |
| **Re-run sector analysis** | `--refresh` flag | Force re-fetch if profiles are >2 weeks old or you added 3+ new holdings. |

---

## Quarterly (Jan · Apr · Jul · Oct — Results Season)

| Task | Script | When | Notes |
|---|---|---|---|
| **Batch data fetch** | `bash scripts/run_batch_analysis.sh --data-only` | Week before results | Gets fresh concalls, shareholding, financials for all watchlist stocks |
| **Batch LLM analysis** | `bash scripts/run_batch_analysis.sh --analysis-only --skip-existing` | During/after results | Generates master_report.md (rating, thesis, catalysts, risks) per company |
| **Investor activity sweep** | `bash scripts/run_investor_activity.sh --min-alloc 0.5` | After results | Check if FIIs entered/exited on results |
| **Sector analysis refresh** | `bash scripts/run_sector_analysis.sh --refresh` | Post-results | Re-classify any companies that pivoted their business mix |
| **Individual deep-dives** | `bash scripts/run_full_analysis.sh SYMBOL` | On surprising results | Re-run single company analysis after major miss or beat |

---

## Occasional (event-driven, no fixed schedule)

| Trigger | Action | Script |
|---|---|---|
| Market correction (−5% or more) | Run watchlist alerts — check if any are at Fresh Entry | `run_watchlist_alerts.sh` |
| Stock-specific news / corporate action | News digest for that specific stock | `run_news_digest.sh` |
| Adding a new position to portfolio | Deep-dive before buying | `run_data_fetch.sh SYM` → `run_full_analysis.sh SYM` |
| Sector theme running hot (AI, Defence, etc.) | Sector analysis to see your exposure | `run_sector_analysis.sh` |
| Large promoter stake change alert | Investor activity for that stock | `run_investor_activity.sh --symbols SYM` |
| Chrome CDP session lost | Re-launch Chrome debug | `bash scripts/launch_chrome_debug.sh` |
| New company added to watchlist | Quick data fetch + analysis | `run_data_fetch.sh SYM` → `run_full_analysis.sh SYM` |
| Portfolio rebalancing needed | Re-check sector gaps after any changes | `run_watchlist_gap_analysis.sh` |

---

## Annual Summary View

```
JAN   FEB   MAR   APR   MAY   JUN   JUL   AUG   SEP   OCT   NOV   DEC
 │     │     │     │     │     │     │     │     │     │     │     │
 ◄─────────────────── Daily: Report + Alerts + News (every weekday) ────────────────────►
 ◄─────────────────── Weekly: Investor Activity + Sector + Gap Analysis ─────────────────►
 │           │           │           │           │           │
[Q4]       [Q1]       [Q2?]       [Q3]       [Q4?]       [Q1?]
Results   Results    MidYr     Results   PreYrEnd   Results
Batch      Batch     Check      Batch      Review     Batch
Analysis  Analysis            Analysis              Analysis

 ◄── Monthly: Sizing review + Watchlist cleanup + Sector refresh ──────────────────────►
```

---

## Key Files

| File | Purpose |
|---|---|
| `watchlist.yaml` | Add/remove companies. Edit manually. |
| `sector_map.yaml` | Manual sector override for daily report. |
| `data/sector_profiles/*.json` | Cached company profiles (30-day TTL). Delete to force refresh. |
| `portfolio/ZV3899/*/` | All report outputs (daily, sector, gaps, investor_activity) |
| `data/companies/*/reports/master_report.md` | Deep-dive research per company |
| `prompts/sector_profile.txt` | LLM prompt for sector classification — edit to tune rules |
| `prompts/watchlist_gap_analysis.txt` | LLM prompt for gap analysis — edit to tune ranking logic |

---

## Windsurf Slash Commands (quick access in IDE)

| Command | Equivalent |
|---|---|
| `/daily_run` | Morning fetch + daily report + alerts + news |
| `/sector_analysis` | Weekly sector profile run |
| `/watchlist_gap_analysis` | Top 3 diversification picks |
| `/investor_activity` | Shareholding tracker |
| `/batch_company_analysis` | Full quarterly batch run |
| `/chrome_debug` | Launch Chrome CDP session |
