# Earnings Season Refactor — Summary & Examples

**Date:** 2026-04-12  
**Status:** ✅ Complete

---

## Overview

The earnings season workflow has been refactored from a **daily snapshot report** to a **quarterly evolving files** architecture, with daily-volatile sections moved to the portfolio daily report.

---

## 1. Daily Evolving Files (Portfolio Report)

**Location:** `portfolio/ZV3899/`  
**Updated by:** `portfolio_daily_report.py`  
**Frequency:** Daily (overwritten each run)

### Files Created

| File | Purpose |
|---|---|
| `dma_deployment.md` | All holdings with DMA40/DMA100 trend classification (Strong Bullish → Bearish) |
| `sector_allocation.md` | Allocation % per sector with bullish/bearish trend count |
| `deployment_candidates.md` | Bullish + under-allocated (< 2%) — where to put money |
| `caution_list.md` | Bearish + significant allocation (≥ 0.5%) — consider reducing |
| `watchlist_not_held.md` | DMA status for watchlist stocks you don't own yet |
| `sme_summary.md` | All SME stocks with trend + allocation |

### Example: DMA Deployment

```markdown
*Updated: 2026-04-12*
## 📈 DMA Deployment Analysis
*DMA40 + DMA100 trend for all holdings — grouped by trend, sorted by allocation*

- 🟢 **Strong Bullish**: 21 stocks, 31.9% of portfolio
- 🟡 **Neutral**: 38 stocks, 29.3% of portfolio
- 🟠 **Bearish Rally**: 31 stocks, 18.1% of portfolio
- 🔴 **Bearish**: 25 stocks, 13.5% of portfolio

| # | Stock | LTP | DMA40 | DMA100 | Trend | Alloc% | P&L% | SME | Sector |
|---|---|---|---|---|---|---|---|---|---|
| 1 | AEROFLEX | ₹289.0 | ₹231 | ₹202 | 🟢 Strong Bullish | 4.7% | +50.7% |  | Specialty Industrials |
| 2 | YATHARTH | ₹753.8 | ₹677 | ₹676 | 🟢 Strong Bullish | 3.7% | +30.1% |  | Healthcare - Hospitals |
...
```

### Example: Deployment Candidates

```markdown
*Updated: 2026-04-12*
## 🎯 Deployment Candidates
*Bullish trend + allocation < 2% — consider increasing position*

| Stock | Trend | Alloc% | LTP | DMA40 | DMA100 | Sector | SME |
|---|---|---|---|---|---|---|---|
| SGFIN | 🟢 Strong Bullish | 0.0% | ₹461.1 | ₹406 | ₹398 | Financial Services - NBFC |  |
| AYMSYNTEX | 🟢 Strong Bullish | 0.3% | ₹192.2 | ₹189 | ₹174 | Textiles - Synthetic Yarn |  |
| ACUTAAS | 🟢 Strong Bullish | 0.5% | ₹2362.2 | ₹2217 | ₹1932 | Specialty Chemicals |  |
...
```

### Example: Caution List

```markdown
*Updated: 2026-04-12*
## ⚠️ Caution List
*Bearish trend + allocation ≥ 0.5% — consider reducing*

| Stock | Trend | Alloc% | LTP | DMA100 | P&L% | Sector |
|---|---|---|---|---|---|---|
| QLL | 🟠 Bearish Rally | 1.7% | ₹349.0 | ₹364 | +5.9% | Pharma - Contract Labs |
| INFLAME | 🔴 Bearish | 1.2% | ₹292.0 | ₹302 | +17.6% | Consumer Durables - Gas A |
| RADIOWALLA-SM | 🔴 Bearish | 1.1% | ₹29.1 | ₹52 | -36.5% | Media - Radio |
...
```

---

## 2. Quarterly Evolving Files (Earnings Season)

**Location:** `data/earnings_season/Mar2026/`  
**Updated by:** `earnings_season.py`  
**Frequency:** Weekly/daily during earnings season (appends/updates)

### Files Created

| File | Purpose |
|---|---|
| `shareholding.md` | Shareholding changes table + tracked investor matrix |
| `results.md` | Per-company quarterly financial comparison (Sales/OPM%/NP/EPS × QoQ/YoY, HoH for SME) |
| `concalls.md` | New transcripts and PPTs per company |

### Example: Shareholding Changes (from old format)

**Old format** showed individual company changes:
```markdown
| Affordable Robotic & Automation Ltd (AFFORDABLE) | NEW: Mar 2026 | 43.6% ▲0.4pp | 1.4% ▲0.2pp | 0.0% | 54.9% ▼0.6pp |  |
| ASM Technologies Ltd (ASMTEC) | NEW: Mar 2026 | 58.0% | 0.2% ▼0.3pp | 0.4% ▲0.1pp | 41.4% ▲0.2pp | 🔔 Mukul Mahavir Agrawa STABLE |
```

**New format** (when data is fetched):
```markdown
# Shareholding — Mar 2026
*Updated: 2026-04-12*

## Shareholding Changes

| Company | Quarter | Promoter | FII | DII | Public |
|---|---|---|---|---|---|
| AFFORDABLE | Mar 2026 | 43.6% ▲0.4pp | 1.4% ▲0.2pp | — | 54.9% ▼0.6pp |
| ASMTEC | Mar 2026 | 58.0% → | 0.2% ▼0.3pp | 0.4% ▲0.1pp | 41.4% ▲0.2pp |
...

## Tracked Investor Matrix

| Company | Mukul Mahavir Agrawal | Ashish Kacholia | Bengal Finance | Suryavanshi | ... |
|---|---|---|---|---|---|
| ASMTEC | 10.28% → | — | — | — | ... |
| AEROFLEX | — | 2.27% ↑ | — | — | ... |
| BETA | — | 5.76% ↓ | — | 6.71% ↓ | ... |
| BCONCEPTS | — | — | — | 1.44% 🆕 | ... |
...
```

### Example: Quarterly Results

**Current state** (Mar 2026 results not yet reported):
```markdown
# Quarterly Results — Mar 2026
*Updated: 2026-04-12*

*136 companies with financial comparison*

### Aeroflex Industries Ltd (AEROFLEX)
| | Mar 2025 | Dec 2025 | Mar 2026 | QoQ | YoY |
|---|---|---|---|---|---|
| Sales | 92 | 121 | — | — | — |
| OPM% | 21% | 23% | — | — | — |
| Net Profit | 11 | 16 | — | — | — |
| EPS | 0.87 | 1.28 | — | — | — |
```

**When Mar 2026 results are reported** (example):
```markdown
### Aeroflex Industries Ltd (AEROFLEX)
| | Mar 2025 | Dec 2025 | Mar 2026 | QoQ | YoY |
|---|---|---|---|---|---|
| Sales | 92 | 121 | 135 | ▲12% | ▲47% |
| OPM% | 21% | 23% | 24% | ▲1.0pp | ▲3.0pp |
| Net Profit | 11 | 16 | 19 | ▲19% | ▲73% |
| EPS | 0.87 | 1.28 | 1.51 | ▲18% | ▲74% |
```

**SME companies** show half-yearly (HoH) instead of quarterly (QoQ):
```markdown
### Shree Refrigerations (SHREEREF)
| | Mar 2025 | Sep 2025 | Mar 2026 | HoH | YoY |
|---|---|---|---|---|---|
| Sales | 48 | 51 | 55 | ▲8% | ▲15% |
| OPM% | 24% | 30% | 28% | ▼2.0pp | ▲4.0pp |
| Net Profit | 4.50 | 8.12 | 9.20 | ▲13% | ▲104% |
| EPS | 1.60 | — | 3.27 | — | ▲104% |
```

### Example: Concalls

```markdown
# Concall Updates — Mar 2026
*Updated: 2026-04-12*

### AEROFLEX (Aeroflex Industries Ltd)
- **Transcripts:** 2 new (Q3FY26, Q4FY26)
- **PPTs:** 1 new (Q4FY26)

### YATHARTH (Yatharth Hospital & Trauma Care Services Ltd)
- **Transcripts:** 1 new (Q4FY26)
```

---

## 3. Key Features

### Watchlist Auto-Sync
The daily report now automatically removes portfolio-held stocks from `watchlist.yaml`:
```
🔄 Watchlist sync: removed 1 held stocks → UGROCAP
```

### Company Filtering
Earnings season now only processes **currently-held + watchlist** companies (skips stale YAML entries):
```
ℹ Skipped 6 companies not in current holdings or watchlist
```

### Trend Classification
Uses **DMA40 + DMA100** for 4-tier classification:
- 🟢 **Strong Bullish**: Price > DMA40 > DMA100
- 🟡 **Neutral**: Price near both DMAs (within ±3%)
- 🟠 **Bearish Rally**: Price > DMA100 but < DMA40
- 🔴 **Bearish**: Price < both DMAs

---

## 4. How to Run

### Daily Report (with evolving files)
```bash
python -m workflows.portfolio_daily_report --user ZV3899
```

### Earnings Season (quarterly files)
```bash
# Delta report only (fast, uses existing data)
python -m workflows.earnings_season

# With shareholding fetch (Chrome CDP auto-launches)
python -m workflows.earnings_season --fetch

# Full data refresh
python -m workflows.earnings_season --fetch-all
```

---

## 5. File Locations Summary

| Type | Location | Updated By | Frequency |
|---|---|---|---|
| Daily evolving | `portfolio/ZV3899/*.md` | portfolio_daily_report.py | Daily |
| Quarterly evolving | `data/earnings_season/Mar2026/*.md` | earnings_season.py | Weekly/daily in season |
| Daily snapshot | `portfolio/ZV3899/holdings/2026-04-10/daily_report.md` | portfolio_daily_report.py | Daily (dated) |
| Old earnings (deprecated) | `portfolio/ZV3899/holdings/2026-04-10/earnings_season.md` | — | No longer generated |

---

## 6. Migration Notes

### What Changed
- ❌ **Removed** from earnings_season.py: DMA deployment, sector allocation, deployment candidates, caution list, watchlist-not-held, SME summary
- ✅ **Added** to portfolio_daily_report.py: 6 daily evolving files in `portfolio/{user}/`
- ✅ **Added** to earnings_season.py: 3 quarterly evolving files in `data/earnings_season/{Quarter}/`
- ✅ **New** investor matrix: pivot table showing tracked investors × companies
- ✅ **New** financial comparison: per-company quarterly results with QoQ/YoY changes
- ✅ **New** SME half-yearly: HoH column instead of QoQ for SME companies
- ✅ **New** watchlist auto-sync: removes held stocks from watchlist.yaml
- ✅ **New** company filtering: only processes held + watchlist companies

### Breaking Changes
- Old `earnings_season.md` format is deprecated
- Output path changed: `portfolio/{user}/holdings/{date}/earnings_season.md` → `data/earnings_season/{Quarter}/`
- DMA/Sector/Deployment sections moved to daily report

---

## 7. Next Steps

1. **Run daily report** to see the 6 evolving files in action
2. **Run earnings season with --fetch** during next results season to populate shareholding changes and investor matrix
3. **Monitor quarterly files** as companies report Q4FY26 results (Apr-May 2026)
4. **Set up cron** for automated daily runs: `bash scripts/cron_setup.sh`

---

**Refactor completed:** 2026-04-12  
**Files modified:** 3 (earnings_season.py, portfolio_daily_report.py, shared_sections.py)  
**New files:** 1 (shared_sections.py)  
**Docs updated:** 2 (.windsurf/workflows/*.md)
