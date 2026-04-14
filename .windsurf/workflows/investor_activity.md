---
description: Investor activity tracker — quarterly shareholding changes (Screener.in) for high-conviction holdings
---

## Investor Activity Tracker

Scrapes Screener.in for **quarterly shareholding data** (Promoter / FII / DII / Public) for:
- All holdings with allocation ≥ 1% (high-conviction threshold)
- All stocks in `watchlist.yaml`
- Any additional symbols you specify

Flags changes ≥ 1.5 percentage-points QoQ and lists notable individual holders (ace investors).

> **Frequency: Weekly or after results season** — shareholding data updates quarterly.
> Not part of `/daily_run` since it's slow (~1.5s per stock, polite to Screener).

### Run (all high-conviction + watchlist)

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.investor_activity
```

### Specific symbols only

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.investor_activity --symbols SYRMA AEROFLEX QPOWER
```

### Lower the allocation threshold

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.investor_activity --min-alloc 2.0
```

Output saved to `portfolio/ZV3899/{today}/investor_activity.md`

### What to look for

| Signal | Interpretation |
|---|---|
| **Promoter stake ▲** | Confident in business — positive |
| **Promoter stake ▼ ≥ 2pp** | Concern — check if pledge or systematic exit |
| **FII ▲ significant** | Institutional interest growing |
| **FII ▼ large** | Institutional exodus — monitor carefully |
| **DII ▲** | Domestic MF / insurance buying — supportive |
| **Ace investor entry/exit** | Follow high-conviction investor actions |
