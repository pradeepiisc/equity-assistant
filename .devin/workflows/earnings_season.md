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
- **Parallel CDP (3 tabs)** — shareholding batch uses 3 concurrent browser tabs, cutting ~20 min → ~7 min
- **Stop-on-block detection** — aborts batch after 10 consecutive failures (possible IP block)
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

### Fetch all (shareholding + financials + transcripts)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch --user ZV3899
```

Shorthand for all 3 phases below. Chrome CDP auto-launches if needed.

### Fetch shareholding only (~7 min with parallel CDP)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch-sh --user ZV3899
```

Best for **weeks 1-3** after quarter end when only shareholding data is available.
Uses 3 concurrent CDP tabs. Companies already at current quarter are skipped.

### Fetch financials only

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch-fin --user ZV3899
```

Best for **weeks 3-8** when quarterly results start appearing. Needs Chrome CDP.

### Fetch transcripts only (no CDP needed)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch-transcripts --user ZV3899
```

Best for **weeks 6-12** when concall transcripts get published. No browser needed.

### Full data refresh (Chrome CDP required)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.earnings_season --fetch-all --user ZV3899
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

### Recommended schedule

| Weeks after quarter end | What to run | Frequency |
|---|---|---|
| **1-3** (Apr/Jul/Oct/Jan) | `--fetch-sh` | Daily or every other day |
| **3-8** | `--fetch-sh --fetch-fin` | 2-3× per week |
| **6-12** | `--fetch` (all 3) | 1-2× per week |
| **Steady state** | (no flag) delta report | Weekly |

### Cron automation

The earnings season cron runs Mon-Sat at 8pm during results months (Jan-Feb, Apr-May, Jul-Aug, Oct-Nov).
Install via: `bash scripts/cron_setup.sh`
