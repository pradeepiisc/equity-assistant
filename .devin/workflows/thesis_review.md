---
description: Post-results thesis review — compare latest quarterly results against existing investment thesis, recommend Add/Hold/Trim/Exit
---

## Post-Results Thesis Review

**When to use:** After quarterly results are declared (weeks 4-10 of each quarter). Reviews whether your investment thesis for each held stock still holds, has strengthened, or has weakened based on latest financial results, shareholding changes, and management commentary.

**Output:**
- Per-company `data/companies/{SYMBOL}/reports/thesis_review.md` + `.json`
- Consolidated `data/earnings_season/{Quarter}/thesis_review_summary.md`
- Portfolio copy `portfolio/{user}/thesis_review_summary.md`

---

### Quick run — dry run (see which companies have results)

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.thesis_review --dry-run
```

### Run thesis review for all companies with results

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.thesis_review --user ZV3899
```

### Fetch latest data first, then review

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.thesis_review --fetch --user ZV3899
```

Uses Chrome CDP for financials/shareholding. Launch Chrome first if needed:
```bash
bash scripts/launch_chrome_debug.sh
```

### Review specific companies

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.thesis_review --symbols DCAL BETA KAYNES --user ZV3899
```

### Include companies without existing master report

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.thesis_review --include-no-thesis --user ZV3899
```

---

### How it works

1. **Identifies companies with results** — scans `data/companies/{SYMBOL}/financials/` for the expected quarter (e.g., Mar 2026)
2. **Loads existing thesis** — master_report.md, valuation_summary.md, valuation_agent.json
3. **Loads fresh data** — latest financials, shareholding, last 4 transcripts (with PDF text extraction), insights_values
4. **Loads holding context** — current price, avg cost, P&L%, allocation, DMA position
5. **Runs LLM analysis** — compares results vs thesis, checks management consistency, evaluates shareholding signals
6. **Produces actionable output** — thesis status (strengthened/intact/weakening/broken), updated conviction score, specific Add/Hold/Trim/Exit with price levels

### What the LLM evaluates

| Dimension | What it checks |
|---|---|
| **Results vs Expectations** | Sales/OPM%/PAT/EPS vs management guidance and thesis estimates |
| **Management Consistency** | Promise tracker — did mgmt deliver on prior commitments? Tone shifts? |
| **Shareholding Signal** | Promoter/FII/DII trends, anchor investor movements |
| **Thesis Delta** | Which assumptions validated, which at risk, growth trajectory change |
| **Forward Outlook** | Next quarter expectations, upgrade/downgrade triggers |

### Recommended schedule

| Timing | What to run |
|---|---|
| **Week 3-4** after quarter end | `--dry-run` to check who's declared |
| **Week 5-8** | `--fetch` then full review for companies with results |
| **Week 8-12** | Re-run as more transcripts become available |
| **After all results** | Final comprehensive review |

### Prerequisites

- Existing `master_report.md` for each company (run `python -m workflows.full_company_analysis {SYMBOL}` first)
- Financial data fetched via earnings_season or data_fetch
- Chrome CDP running for --fetch mode (shareholding/financials)
- Transcripts downloaded (concall_fetcher or earnings_season --fetch-transcripts)
