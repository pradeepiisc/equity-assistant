---
description: Sector analysis workflow — LLM-enriched company profiles grouped by output industry (run weekly)
---

## Sector Analysis — Weekly Workflow

This is a **separate, weekly workflow** that replaces the old static sector labels. Instead of a single sector tag, each company gets a rich profile describing:

- **What the company does** — plain English business description
- **Key products / services** — what they make or sell
- **Input sensitivity** — what drives their costs (steel, chemicals, interest rates, etc.)
- **Output industries** — which end-markets consume their products (AI/Data Centers, Railways, Oil & Gas, FMCG, etc.)

A company like Aeroflex Industries will appear under **multiple output industries**: Industrial Capex AND AI/Data Centers — because their steel pipes serve both.

---

### Step 1 — Run sector analysis (full portfolio, first run will take a while)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.sector_analysis
```

First run: fetches ~127 companies from Screener.in + LLM enrichment (~30–40 min).
Subsequent runs: uses 30-day cache, only re-fetches new/expired companies (~1–2 min).

### Step 2 — Run for specific companies only

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.sector_analysis --symbols AEROFLEX HFCL MANAPPURAM
```

### Step 3 — Force refresh (ignore cache)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.sector_analysis --refresh
```

### Step 4 — Scrape only (no LLM, faster)

```bash
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python -m workflows.sector_analysis --no-llm
```

---

### What the report covers

| Section | What it tells you |
|---|---|
| **Holdings by Output Industry** | All companies grouped by end-market theme (AI/Data Centers, Railways, FMCG, etc.) — total portfolio % per theme |
| **Input Cost Sensitivity** | Which companies are exposed to steel, chemicals, oil, interest rates — useful for macro risk |
| **Company Profiles** | Per-company: business description, key products, end use, output industries |

---

### Cache behaviour

- Profiles cached in `data/sector_profiles/{SYMBOL}.json`
- 30-day TTL — re-fetched automatically when expired
- Report saved to `portfolio/ZV3899/sector/{date}/sector_analysis.md`
- If a company is newly added to your portfolio, it will be fetched on next run

### Why not in the daily report?

- LLM enrichment takes time; not appropriate for a daily routine
- Sector themes don't change day-to-day — weekly is sufficient
- Screener.in rate limits make bulk daily fetching impractical
- The insight is strategic (which themes are you concentrated in?) not operational (DMA, P&L)

### Why better than a static sector label?

Static labels collapse nuance:
- **BLS International** → labelled "IT Services" but actually does **Visa and Tourism Services** (not IT at all)
- **GANESHCP** → labelled "Chemicals" but their primary business is **consumer products (personal care)**
- **Aeroflex** → labelled "Steel" but serves **HVAC, Oil & Gas, and AI data center cooling**

The LLM-generated profiles capture multiple dimensions and you can query them naturally.
