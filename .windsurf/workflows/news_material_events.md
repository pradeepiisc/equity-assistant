---
description: End-to-end news material-events scan — fetch digest + generate filtered alerts
---

## News Material Events (End-to-End)

Runs a complete scan in one command:
1. Fetches latest Google News RSS headlines for top holdings + watchlist
2. Filters potentially material events using deterministic keywords
3. Saves a compact actionable report

### Run (recommended)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_material_events --user ZV3899 --top 200
```

### Reuse existing digest (faster)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.news_material_events --user ZV3899 --skip-fetch --date 2026-03-02
```

### Outputs

Saved under `portfolio/ZV3899/holdings/<snapshot_date>/`:
- `news_digest.md` — full headline digest
- `news_material_events.md` — filtered material-events report

### Notes
- Deterministic filtering only (no LLM call)
- Designed as an alert layer; open links for confirmation/context
- Edit keyword buckets in `workflows/news_material_events.py` to tune sensitivity
