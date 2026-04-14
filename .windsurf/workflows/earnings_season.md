---
description: Earnings season delta report — quarterly evolving files (shareholding, results, concalls), tracked investor matrix, new company onboarding
---

## Earnings Season Report

**When to use:** During quarterly results season (Jan-Feb, Apr-May, Jul-Aug, Oct-Nov).
Run weekly or daily to catch new results, transcripts, and shareholding changes.

**Output:** Evolving quarterly files in `data/earnings_season/{Quarter}/`:
- `shareholding.md` — shareholding changes table + tracked investor matrix
- `results.md` — per-company quarterly financial comparison (Sales/OPM%/NP/EPS × QoQ/YoY, HoH for SME)
- `concalls.md` — new transcripts and PPTs per company

Key features:
- **Shareholding delta detection** — fetches fresh data from Screener.in via Chrome CDP (real browser, auto-launches if needed), compares with existing files, shows actual QoQ changes (e.g., Promoter +2.6pp, FII -0.7pp)
- **Stop-on-block detection** — aborts batch after 5 consecutive failures (possible IP block); jittered 3-6s delay between page loads
- **Tracked investor matrix** — companies as rows × tracked investors as columns, showing latest% with trend arrows (🆕/↑/↓/→)
- **Quarterly results comparison** — per-company tables with previous year, previous quarter, current quarter, QoQ and YoY changes; SME companies show half-yearly numbers with HoH
- **New company onboarding** — inline onboard via `--onboard` flag

> **Note:** DMA deployment, sector allocation, deployment candidates, caution list, SME summary, and watchlist-not-held sections have moved to `portfolio_daily_report.py` as daily evolving files in `portfolio/{user}/`.

---

### Quick run (delta report only — fast, no network except DMA)

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season
```

### With shareholding fetch + transcripts (Chrome CDP auto-launches)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch
```

This fetches fresh shareholding from Screener.in via Chrome CDP (auto-launches Chrome debug session if not running). You'll see pages opening/closing in Chrome as it browses Screener for each company. Compares with existing data to show actual percentage-point changes. Companies already at the current quarter are automatically skipped.

### Full data refresh (Chrome CDP required)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch-all
```

Chrome will auto-launch if not running. For manual launch:
```bash
bash scripts/launch_chrome_debug.sh
```

### Specific user or symbols

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --user ZV3899
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --symbols QPOWER BETA KAYNES
```

### First run / ignore previous state

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --full-refresh
```

### Onboard a new company inline

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch --onboard "SYMBOL:Company Name:https://www.screener.in/company/SYMBOL/"
```

This creates the company folder, fetches available data, adds to watchlist, then runs the earnings report.

Output saved to `data/earnings_season/{Quarter}/` (e.g. `data/earnings_season/Mar2026/`)

State tracked in `portfolio/.cache/earnings_season_state.json`

---

### Quarterly evolving files

| File | Contents |
|---|---|
| **shareholding.md** | Shareholding changes table (Promoter/FII/DII/Public with QoQ pp changes) + tracked investor matrix |
| **results.md** | Per-company quarterly financial comparison: Sales, OPM%, Net Profit, EPS × QoQ/YoY (HoH/YoY for SME) |
| **concalls.md** | New transcripts and PPTs per company with counts |

### Daily evolving files (moved to portfolio_daily_report.py)

These are now written by the daily report to `portfolio/{user}/`:

| File | What it tells you |
|---|---|
| **dma_deployment.md** | All holdings with DMA40/DMA100 trend classification |
| **sector_allocation.md** | Allocation % per sector with bullish/bearish trend count |
| **deployment_candidates.md** | Bullish + under-allocated — where to put money |
| **watchlist_not_held.md** | DMA status for watchlist stocks you don't own yet |
| **caution_list.md** | Bearish + significant allocation — consider reducing |
| **sme_summary.md** | All SME stocks with trend + allocation |

### Cron automation

The earnings season cron runs Mon-Sat at 8pm during results months (Jan-Feb, Apr-May, Jul-Aug, Oct-Nov).
Install via: `bash scripts/cron_setup.sh`
