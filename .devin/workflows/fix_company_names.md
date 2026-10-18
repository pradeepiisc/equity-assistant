---
description: Fix company names — scrape correct names from Screener.in via Chrome CDP and update YAMLs + meta.json
---

## Prerequisites
Chrome must be running in debug mode on port 9222.
Start it from Terminal.app (NOT Windsurf terminal):
```
bash scripts/launch_chrome_debug.sh
```

## Steps

1. Verify Chrome is running
```bash
python -c "from skills.cdp_helper import is_available; print('Chrome available:', is_available())"
```

// turbo
2. Run fix for all companies (scrapes h1 from each Screener page)
```bash
python -m workflows.fix_company_names
```

3. To fix specific companies only:
```bash
python -m workflows.fix_company_names --symbols SHREEREF QLL ACLD INDOSMC HIRECT TEXELIN
```

4. Dry-run to preview changes without writing:
```bash
python -m workflows.fix_company_names --dry-run
```

## What this does
- Visits each company's Screener.in page via Chrome CDP
- Extracts the authoritative company name from the `<h1>` tag
- Saves to `data/companies/{SYMBOL}/meta.json`
- Updates `name` field in `portfolio_companies.yaml` and `watchlist.yaml`

## After running
All downstream tools (growth_signals, growth_ranking, valuation_agent, news_fetcher)
automatically read from meta.json for correct names.
