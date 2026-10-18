---
description: Exit Strategy End-to-End Workflow — Generate Deep Reports & Consolidate
---

# Exit Strategy End-to-End Workflow

This workflow iterates over **all** companies present in `data/companies/` and runs the enhanced deep investment assessment (using existing data files) to generate updated `master_report.md` files containing the explicit **Decision Framework** (Invest/Stay/Trim/Exit). Finally, it consolidates all of these decisions into a single master dashboard.

## Run the End-to-End Workflow

```bash
# turbo
bash scripts/run_all_and_consolidate.sh
```

### What this does:
1. **Batch Analysis:** Runs `python -m workflows.batch_company_analysis --all --analysis-only`. This skips data fetching and solely relies on the already-downloaded transcripts, financials, etc.
2. **Consolidation:** Runs `python -m workflows.consolidated_exit_strategy`. This reads the newly generated `master_report.json` files and extracts the ratings, fair values, momentum, and explicit entry/exit triggers into one place.

**Output:** `portfolio/consolidated_exit_strategy.md`
**Runtime:** ~5–10 minutes per company with data.
