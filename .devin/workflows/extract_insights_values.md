---
description: Extract Screener Insights metric values from transcripts/PPTs via LLM — saves quarterly table to insights/insights_values.txt per company
---

## What this does
For each company, reads the Screener Insights metric labels (e.g., "Number of order items processed")
and extracts their actual quarterly values from earnings call transcripts and investor PPTs using LLM.
Saves a markdown table with metrics as rows and quarters as columns.

## Prerequisites
- Company transcripts/PPTs must already be downloaded (`python -m workflows.data_fetch SYMBOL`)
- Screener Insights file must exist (`python -m workflows.ensure_company_data --insights-only`)

## Run for a single company
```bash
python -m skills.insights_values UNIECOM
```

## Run for specific companies
```bash
python -m workflows.extract_insights_values --symbols UNIECOM QPOWER ACUTAAS
```

## Run for all companies (batch)
```bash
python -m workflows.extract_insights_values
```

## Run with options
```bash
# Force re-extraction even if insights_values.txt already exists
python -m workflows.extract_insights_values --refresh

# Process first 20 companies only
python -m workflows.extract_insights_values --batch-size 20

# Skip first 20 and process the rest
python -m workflows.extract_insights_values --offset 20
```

## Output
- `data/companies/{SYMBOL}/insights/insights_values.txt` — markdown table (metrics × quarters)
- `data/companies/{SYMBOL}/insights/insights_values.json` — raw JSON from LLM

## Table format example
```
| Metric                              | Q3 FY26       | Q2 FY26       | Q1 FY26       |
|-------------------------------------|---------------|---------------|---------------|
| Number of order items processed     | 1,200 million | 1,100 million | 1,000 million |
| Total Enterprise Clients (Uniware)  | 1,050         | 980           | 900           |
```
