---
description: Growth ranking — LLM-based trajectory clustering of all portfolio companies using transcripts + PPTs + Screener Insights
---

## Growth Ranking Workflow

Analyses every portfolio company (excluding GAUDIUMIVF, BEWLTD-SM) using their earnings call transcripts, investor PPTs, and Screener Insights to produce a growth_signals report per company and a master portfolio growth ranking report.

### Steps

1. Verify Chrome CDP is running (required for any Screener data fetch):
```bash
python -c "from skills.cdp_helper import is_available; print('Chrome:', 'OK' if is_available() else 'NOT RUNNING')"
```

2. Run growth ranking for all companies (processes ~136 companies; skips those already done):
```bash
python -m workflows.growth_ranking 2>&1 | tee /tmp/growth_ranking.log
```

3. To run in batches of 40 (add --offset N to continue):
```bash
python -m workflows.growth_ranking --batch-size 40 --offset 0
python -m workflows.growth_ranking --batch-size 40 --offset 40
python -m workflows.growth_ranking --batch-size 40 --offset 80
python -m workflows.growth_ranking --batch-size 40 --offset 120
```

4. To refresh a specific company (force re-run):
```bash
python -m skills.growth_signals SYMBOL --refresh
```

5. View the ranking report:
```
portfolio/ZV3899/growth_ranking_{YYYYMMDD}.md
```

### Output per company
- `data/companies/{SYMBOL}/reports/growth_signals.md`
- `data/companies/{SYMBOL}/reports/growth_signals.json`

### Valuation integration
Growth signals are automatically picked up by `valuation_agent.py` — if `reports/growth_signals.json` exists, the LLM valuation prompt receives the trajectory, bottom-line outlook, key drivers, and red flags as additional context.
