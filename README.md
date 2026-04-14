# Equity Research Assistant

A personal AI-powered equity research system for an active investor. Covers the full daily workflow: portfolio health, DMA signals, conviction reviews, company deep-dives, news digest, and watchlist entry alerts. All analysis is local, LLM-assisted, and grounded in real data fetched from Screener.in, Zerodha Kite, yfinance, and BSE filings.

---

## Module Map & Dependency Graph

```
Inputs (data sources)
─────────────────────
  Zerodha Kite API          → integrations/kite_connect.py
  yfinance / BSE / NSE      → skills/dma_fetcher.py
  Screener.in (CDP)         → skills/cdp_helper.py, skills/company_meta.py
  BSE / NSE concall PDFs    → skills/concall_fetcher.py
  Google News RSS           → workflows/news_digest.py

Core LLM Layer
──────────────
  llm/client.py   ← AzureOpenAI multi-config (gpt-4o / gpt-4.1 / gpt-5.2)
  llm/utils.py    ← load_prompt(), call_llm(), save_report(), load_company_files()
  prompts/        ← one .txt prompt per skill

Skills (stateless, one company at a time)
─────────────────────────────────────────
  skills/transcript_analysis.py    → PDFs → LLM → growth, guidance, red flags
  skills/financial_snapshot.py     → financials/ → LLM → revenue/margin/debt
  skills/valuation_agent.py        → sector prompt + financials + transcripts → fair value
  skills/growth_signals.py         → transcripts + Screener insights → growth bucket
  skills/insights_values.py        → transcripts/PPTs → quarterly metric table (.md) + source citations
  skills/news_sentiment.py         → news/ → LLM → sentiment, themes
  skills/shareholding_analysis.py  → shareholding/ → LLM → promoter/FII trends
  skills/peer_comparison.py        → financials/ → LLM → relative valuation
  skills/dma_fetcher.py            → yfinance → 50/200 DMA (NSE+BSE fallback, cached daily)
  skills/concall_fetcher.py        → BSE XML → PDF download → data/companies/{SYM}/transcripts/
  skills/cdp_helper.py             → Chrome CDP → Screener.in scraping
  skills/company_meta.py           → Screener.in → name, BSE code, sector
  skills/valuepickr_fetcher.py     → ValuePickr forum (Discourse API) → expert discussion → valuepickr.md

Workflows (orchestrators, multi-company or multi-skill)
───────────────────────────────────────────────────────
  workflows/full_company_analysis.py      → all skills → master_report (watchlist + portfolio)
  workflows/batch_company_analysis.py     → full analysis for many companies in batch
  workflows/conviction_builder.py  ★NEW  → DMA + holding data + analysis → Add/Hold/Trim/Exit
  workflows/portfolio_daily_report.py     → Kite holdings + DMA → daily health report
  workflows/watchlist_alerts.py           → DMA signals for watchlist stocks not yet held
  workflows/data_fetch.py                 → concalls + news + shareholding fetch
  workflows/extract_insights_values.py    → batch insights_values skill for portfolio companies
  workflows/growth_ranking.py             → growth_signals for all companies → ranked list
  workflows/sector_analysis.py           → sector profiles → grouped company comparison
  workflows/investor_activity.py          → shareholding changes for high-conviction holdings
  workflows/top_holdings_valuation.py     → valuation_agent for top N holdings
  workflows/news_digest.py                → news headlines for top holdings + watchlist
  workflows/news_material_events.py       → filter digest for actionable alerts
  workflows/onboard_company.py            → add to watchlist + full data + analysis pipeline (one command)
  workflows/earnings_season.py            → CDP shareholding delta + transcripts + DMA → quarterly report

Data store (local filesystem)
─────────────────────────────
  data/companies/{SYMBOL}/
    transcripts/     ← earnings call PDFs
    financials/      ← P&L, balance sheet (txt/json/csv)
    shareholding/    ← quarterly shareholding JSON
    news/            ← news text files
    ppt/             ← investor presentations
    insights/        ← Screener Insights (.txt) + insights_values.md
    reports/         ← skill outputs (.md + .json per skill)
    valuepickr.md    ← ValuePickr forum expert analysis (fetched by valuepickr_fetcher)

  portfolio/{USER}/
    holdings/{date}/
      holdings.json          ← daily Kite snapshot
      daily_report.md        ← portfolio health report
      conviction_review.md   ← conviction builder output  ★NEW
    .cache/dma_{date}.json   ← DMA values cached per day

Configuration
─────────────
  watchlist.yaml             ← stocks to watch (not yet held or high focus)
  portfolio_companies.yaml   ← all portfolio holdings (~115 stocks)
  sector_map.yaml            ← symbol → sector mapping
  config.yaml                ← LLM, paths, skill toggles
  .env                       ← API keys (never commit)
```

---

## Use Case Coverage

| Use Case | Command | Output |
|---|---|---|
| **Morning portfolio health** | `python -m workflows.portfolio_daily_report` | `portfolio/.../daily_report.md` |
| **DMA reduce/add signals** | (included in daily report) | DMA Status section |
| **Conviction review (daily losers)** | `python -m workflows.conviction_builder --top-losers 5` | `conviction_review.md` |
| **Conviction review (specific stocks)** | `python -m workflows.conviction_builder --symbols SYM1 SYM2` | `conviction_review.md` |
| **Watchlist entry alerts** | `python -m workflows.watchlist_alerts` | console + markdown |
| **News digest (top holdings + watchlist)** | `python -m workflows.news_digest` | `portfolio/.../news_digest.md` |
| **Full company deep-dive** | `python -m workflows.full_company_analysis SYM` | `data/.../reports/master_report.md` |
| **Quarterly insights extraction** | `python -m workflows.extract_insights_values --symbols SYM` | `data/.../insights/insights_values.md` |
| **Onboard new watchlist company** | `python -m workflows.onboard_company SYM --name "..." --sector "..."` | all data + master_report in one run |
| **Fetch new concalls + news** | `python -m workflows.data_fetch SYM` | `data/.../transcripts/*.pdf` |
| **Growth ranking (all portfolio)** | `python -m workflows.growth_ranking` | `portfolio/.../growth_ranking.md` |
| **Valuation (top N holdings)** | `python -m workflows.top_holdings_valuation` | per-company valuation reports |
| **Sector comparison** | `python -m workflows.sector_analysis` | `data/sector_profiles/*.json` |

---

## Daily Workflow

```bash
# 1. Login (once per day — token expires daily)
python -m integrations.kite_connect --login

# 2. Save today's portfolio snapshot
python -m integrations.kite_connect --save-portfolio

# 3. Generate daily health report (includes DMA for priority stocks)
python -m workflows.portfolio_daily_report

# 4. Conviction review for top 5 losers
python -m workflows.conviction_builder --top-losers 5

# 5. Watchlist entry alerts
python -m workflows.watchlist_alerts

# 6. News digest
python -m workflows.news_digest
```

---

## DMA Notes

- **Source**: yfinance primary (`.NS` then `.BO` fallback), Screener.in CDP fallback
- **Calculation**: 50-day and 200-day simple moving average of closing prices
- **Values confirmed accurate** against Yahoo Finance historical data (same values, verified for DCAL)
- **Cache**: `portfolio/.cache/dma_{date}.json` — rebuilt once per day
- **Coverage**: All priority holdings (watchlist ∪ top-15 losers ∪ ≥3% alloc ∪ top-20 by value)

---

## Key Skills — Input → Output

| Skill | Input | Prompt | Output |
|---|---|---|---|
| `transcript_analysis` | transcripts/*.pdf | transcript_analysis.txt | growth, guidance, red_flags |
| `financial_snapshot` | financials/* | financial_snapshot.txt | revenue trend, margins, debt |
| `valuation_agent` | financials + transcripts + sector prompt | sector_valuation/*.txt | fair value range, entry signal |
| `growth_signals` | transcripts + PPTs + Screener insights | growth_signals.txt | growth_score 1–10, trajectory |
| `insights_values` | transcripts + PPTs | insights_values.txt | quarterly metric table + source citations |
| `news_sentiment` | news/*.txt | news_sentiment.txt | sentiment, themes, risks |
| `shareholding_analysis` | shareholding/* | shareholding_analysis.txt | promoter/FII trends |
| `conviction_builder` | holding data + DMA + all above | conviction_review.txt | Add/Hold/Trim/Exit + price levels |

---

## Setup

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your Azure OpenAI credentials:

```
AZURE_OPENAI_API_KEY=your_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
```

---

## How to Add a Company to the Watchlist

Edit `watchlist.yaml` and add a new entry under `stocks:`:

```yaml
- symbol: TATAMOTORS
  name: Tata Motors Limited
  exchange: NSE
  sector: Automobiles
  screener_url: https://www.screener.in/company/TATAMOTORS/
  bse_code: "500570"
  watch_reason: "EV transition play"
  peers:
    - MARUTI
    - M&M
```

Then create the required data folders:

```bash
mkdir -p data/companies/TATAMOTORS/{transcripts,news,shareholding,financials,reports}
```

---

## How to Add Transcripts Manually

1. Download the earnings call PDF from Screener.in or BSE
2. Name it using the convention: `SYMBOL_Q1FY25.pdf`
3. Place it in: `data/companies/{SYMBOL}/transcripts/`

Example:

```
data/companies/QPOWER/transcripts/QPOWER_Q1FY25.pdf
data/companies/QPOWER/transcripts/QPOWER_Q2FY25.pdf
```

Or auto-fetch using:

```bash
python -m skills.concall_fetcher QPOWER
```

---

## How to Run

### Single company (full analysis)

```bash
python -m workflows.full_company_analysis QPOWER
```

### Full watchlist

```bash
python -m workflows.watchlist_monitor
```

### Quarterly review (fetch new transcripts + analyse)

```bash
python -m workflows.quarterly_review QPOWER   # single company
python -m workflows.quarterly_review           # all watchlist companies
```

### Run a single skill in isolation

```python
from llm.client import get_config
from skills import transcript_analysis
import yaml

config = yaml.safe_load(open("config.yaml"))
company = {"symbol": "QPOWER", "name": "Q Power Limited", "sector": "Power & Energy",
           "screener_url": "https://www.screener.in/company/QPOWER/"}
result = transcript_analysis.run(company, config)
print(result["data"])
```

---

## Folder Structure

```
equity-assistant/
├── data/
│   └── companies/
│       └── QPOWER/
│           ├── transcripts/      ← place earnings call PDFs here
│           ├── news/             ← place news text files here (.txt)
│           ├── shareholding/     ← shareholding JSON/CSV files
│           ├── financials/       ← P&L / balance sheet data (txt/json/csv)
│           └── reports/          ← generated markdown + JSON reports
├── llm/
│   ├── client.py                 ← Azure OpenAI client, get_config()
│   └── utils.py                  ← load_prompt, call_llm, save_report, etc.
├── prompts/
│   ├── transcript_analysis.txt
│   ├── shareholding_analysis.txt
│   ├── news_sentiment.txt
│   ├── financial_snapshot.txt
│   ├── peer_comparison.txt
│   └── master_report.txt
├── skills/
│   ├── transcript_analysis.py
│   ├── shareholding_analysis.py
│   ├── news_sentiment.py
│   ├── financial_snapshot.py
│   ├── peer_comparison.py
│   └── concall_fetcher.py        ← auto-downloads PDFs from Screener
├── workflows/
│   ├── full_company_analysis.py  ← orchestrates all skills → master report
│   ├── watchlist_monitor.py      ← runs full analysis for every company
│   └── quarterly_review.py       ← fetch + analyse (run at results time)
├── watchlist.yaml                ← your stocks (symbol, screener URL, peers)
├── config.yaml                   ← model settings, paths, toggles
├── .env                          ← API keys (never commit)
├── .env.example                  ← safe template
└── requirements.txt
```

---

## Output

For each company, reports are saved to `data/companies/{SYMBOL}/reports/`:

| File | Contents |
|------|----------|
| `transcript_analysis.md` | Management tone, guidance vs delivery, red flags |
| `shareholding_analysis.md` | FII/DII/promoter trend analysis |
| `news_sentiment.md` | News themes, regulatory mentions, sentiment |
| `financial_snapshot.md` | Revenue/margin/debt/cash flow assessment |
| `peer_comparison.md` | Relative valuation and competitive position |
| `master_report.md` | Final investment thesis, rating, action recommendation |
| `*.json` | Raw LLM JSON alongside each markdown report |

---

## Configuration

Key settings in `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `llm.deployment` | `gpt-4.1` | Azure OpenAI deployment name |
| `llm.temperature` | `0.1` | Lower = more deterministic |
| `llm.max_tokens` | `4096` | Max response tokens |
| `skills.max_transcripts_per_company` | `5` | Last N quarters to analyse |
| `output.save_json_alongside` | `true` | Save raw JSON next to markdown |
