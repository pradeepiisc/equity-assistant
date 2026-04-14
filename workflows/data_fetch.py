"""
Workflow: Data Fetch
=====================
Fetches all raw data for a company before analysis:
  1. Concall transcripts   → data/companies/{SYMBOL}/transcripts/
  2. News articles         → data/companies/{SYMBOL}/news/
  3. Shareholding pattern  → data/companies/{SYMBOL}/shareholding/

Usage:
    python -m workflows.data_fetch DCAL
    python -m workflows.data_fetch QPOWER --transcripts-only
    python -m workflows.data_fetch DCAL --skip-news

After this workflow, run the full analysis:
    python -m workflows.full_company_analysis DCAL
"""

import argparse
import sys
import yaml
from pathlib import Path

from llm.client import get_config
from skills import concall_fetcher, news_fetcher, shareholding_fetcher, financial_fetcher, insights_fetcher, valuepickr_fetcher
from skills.cdp_helper import ensure_chrome_running

PROJECT_ROOT = Path(__file__).parent.parent


def _load_company(symbol: str) -> dict:
    """Load company entry from watchlist.yaml or portfolio_companies.yaml."""
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        yaml_path = PROJECT_ROOT / yaml_file
        if not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        company = next(
            (c for c in data.get("stocks", [])
             if c.get("symbol", "").upper() == symbol.upper()
             or str(c.get("screener_symbol", "")).upper() == symbol.upper()),
            None,
        )
        if company is not None:
            return company
    print(f"[ERROR] Symbol '{symbol}' not found in watchlist.yaml or portfolio_companies.yaml.")
    sys.exit(1)


def _print_result(label: str, result: dict) -> None:
    status = result.get("status", "unknown")
    error = result.get("error")
    if status == "success":
        data = result.get("data", {})
        if isinstance(data, dict):
            saved = data.get("saved", data.get("downloaded", []))
            skipped = data.get("skipped", [])
            n_saved = len(saved) if isinstance(saved, list) else (1 if saved else 0)
            n_skipped = len(skipped)
            print(f"  [{label}] ✓  saved={n_saved}  skipped={n_skipped}")
        else:
            print(f"  [{label}] ✓")
    elif status == "no_data":
        print(f"  [{label}] –  no_data: {error}")
    else:
        print(f"  [{label}] ✗  error: {error}")


def run(symbol: str,
        fetch_transcripts: bool = True,
        fetch_news: bool = True,
        fetch_shareholding: bool = True,
        fetch_financials: bool = True,
        fetch_insights: bool = True,
        fetch_valuepickr: bool = True,
        transcript_limit: int = 4,
        news_limit: int = 5,
        refresh_financials: bool = False,
        refresh_insights: bool = False,
        interactive: bool = True) -> dict:
    """
    Run all data fetch steps for a company.

    Steps:
      1. Concall Transcripts + PPTs  (concall_fetcher)
      2. News Articles               (news_fetcher)
      3. Shareholding Pattern        (shareholding_fetcher)
      4. Financial Data              (financial_fetcher)
      5. Screener Insights           (insights_fetcher)
      6. ValuePickr Forum Thread     (valuepickr_fetcher)

    Returns:
        dict with per-step results
    """
    symbol = symbol.upper()
    company = _load_company(symbol)
    config = get_config()

    print(f"\n{'=' * 60}")
    print(f"  DATA FETCH: {symbol}")
    print(f"{'=' * 60}")
    print(f"  Company : {company.get('name', symbol)}")
    print(f"  Sector  : {company.get('sector', 'N/A')}")
    print()

    results = {}

    # ── 1. Concall Transcripts + PPTs ──────────────────────────────────────
    if fetch_transcripts:
        print(f"{'─' * 60}")
        print(f"  Step 1/5 — Concalls: Transcripts + PPTs (last {transcript_limit} each)")
        print(f"{'─' * 60}")
        r = concall_fetcher.run(company, config, limit=transcript_limit)
        results["concalls"] = r
        d = r.get("data", {})
        n_t = len(d.get("transcripts_downloaded", [])) + len(d.get("transcripts_skipped", []))
        n_p = len(d.get("ppts_downloaded", [])) + len(d.get("ppts_skipped", []))
        status = r.get("status", "?")
        icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
        print(f"  [concalls] {icon}  transcripts={n_t}  ppts={n_p}  [{status}]")
    else:
        print("  Step 1/5 — Concalls  [skipped]")

    # ── 2. News Articles ───────────────────────────────────────────────────
    if fetch_news:
        print(f"\n{'─' * 60}")
        print(f"  Step 2/5 — News Articles (last {news_limit})")
        print(f"{'─' * 60}")
        r = news_fetcher.run(company, config, limit=news_limit)
        results["news"] = r
        _print_result("news", r)
    else:
        print("  Step 2/5 — News Articles  [skipped]")

    # ── CDP pre-flight (shareholding, financials, insights all need Chrome) ─
    _cdp_steps_wanted = fetch_shareholding or fetch_financials or fetch_insights
    if _cdp_steps_wanted:
        _cdp_ok = ensure_chrome_running()
        if not _cdp_ok:
            print("  [cdp] ⚠️  CDP steps will be skipped (shareholding / financials / insights).")
            print("  [cdp] To run them later: bash scripts/launch_chrome_debug.sh  then  python -m workflows.data_fetch " + symbol)
            fetch_shareholding = False
            fetch_financials = False
            fetch_insights = False

    # ── 3. Shareholding Pattern ────────────────────────────────────────────
    if fetch_shareholding:
        print(f"\n{'─' * 60}")
        print(f"  Step 3/5 — Shareholding Pattern")
        print(f"{'─' * 60}")
        r = shareholding_fetcher.run(company, config)
        results["shareholding"] = r
        _print_result("shareholding", r)
    else:
        print("  Step 3/5 — Shareholding Pattern  [skipped]")

    # ── 4. Financial Data ──────────────────────────────────────────────────
    if fetch_financials:
        print(f"\n{'─' * 60}")
        print(f"  Step 4/5 — Financial Data (P&L, Balance Sheet, Cash Flow, Ratios)")
        print(f"{'─' * 60}")
        r = financial_fetcher.run(company, config, refresh=refresh_financials)
        results["financials"] = r
        _print_result("financials", r)
    else:
        print("  Step 4/5 — Financial Data  [skipped]")

    # ── 5. Screener Insights ───────────────────────────────────────────────
    if fetch_insights:
        print(f"\n{'─' * 60}")
        print(f"  Step 5/5 — Screener Insights (Beta)")
        print(f"{'─' * 60}")
        r = insights_fetcher.run(company, config, refresh=refresh_insights)
        results["insights"] = r
        d = r.get("data", {})
        n_ins = d.get("insights_count", 0)
        status = r.get("status", "?")
        icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
        print(f"  [insights] {icon}  {n_ins} insight(s) found  [{status}]")
    else:
        print("  Step 5/5 — Screener Insights  [skipped]")

    # ── 6. ValuePickr Forum Thread ─────────────────────────────────────────
    if fetch_valuepickr:
        print(f"\n{'─' * 60}")
        print(f"  Step 6/6 — ValuePickr Forum Thread")
        print(f"{'─' * 60}")
        try:
            vp_path = valuepickr_fetcher.run(
                symbol=symbol,
                company_name=company.get("name"),
                verbose=False,
                interactive=interactive,
            )
            if vp_path:
                results["valuepickr"] = {"status": "success", "data": {"path": str(vp_path)}}
                print(f"  [valuepickr] ✓  saved → {vp_path.name}")
            else:
                results["valuepickr"] = {"status": "no_data", "error": "no thread found"}
                print(f"  [valuepickr] –  no thread found on ValuePickr")
        except Exception as e:
            results["valuepickr"] = {"status": "error", "error": str(e)}
            print(f"  [valuepickr] ✗  {e}")
    else:
        print("  Step 6/6 — ValuePickr Forum  [skipped]")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  DATA FETCH COMPLETE: {symbol}")
    print(f"{'=' * 60}")
    for step, r in results.items():
        status = r.get("status", "?")
        icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
        print(f"  {icon}  {step:<20} [{status}]")
    print()
    print("  Next step:")
    print(f"    python -m workflows.full_company_analysis {symbol}")
    print()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch raw data for a company (watchlist or portfolio)")
    parser.add_argument("symbol", help="Company symbol (e.g. DCAL)")
    parser.add_argument("--transcripts-only", action="store_true",
                        help="Only download concall transcripts + PPTs")
    parser.add_argument("--skip-news", action="store_true",
                        help="Skip news article fetching")
    parser.add_argument("--skip-transcripts", action="store_true",
                        help="Skip transcript + PPT downloading")
    parser.add_argument("--skip-shareholding", action="store_true",
                        help="Skip shareholding pattern scraping")
    parser.add_argument("--skip-financials", action="store_true",
                        help="Skip financial data scraping")
    parser.add_argument("--skip-insights", action="store_true",
                        help="Skip Screener insights scraping")
    parser.add_argument("--skip-valuepickr", action="store_true",
                        help="Skip ValuePickr forum thread fetch")
    parser.add_argument("--refresh-financials", action="store_true",
                        help="Force re-fetch financial data even if already fetched today")
    parser.add_argument("--refresh-insights", action="store_true",
                        help="Force re-fetch insights even if already fetched today")
    parser.add_argument("--transcript-limit", type=int, default=8,
                        help="Max concall PDFs to download per type (default 4)")
    parser.add_argument("--news-limit", type=int, default=5,
                        help="Max news articles to fetch (default 5)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Disable interactive selection for ValuePickr threads (auto-select best match)")
    args = parser.parse_args()

    if args.transcripts_only:
        args.skip_news = True
        args.skip_shareholding = True
        args.skip_financials = True
        args.skip_insights = True

    run(
        symbol=args.symbol,
        fetch_transcripts=not args.skip_transcripts,
        fetch_news=not args.skip_news,
        fetch_shareholding=not args.skip_shareholding,
        fetch_financials=not args.skip_financials,
        fetch_insights=not args.skip_insights,
        fetch_valuepickr=not args.skip_valuepickr,
        transcript_limit=args.transcript_limit,
        news_limit=args.news_limit,
        refresh_financials=getattr(args, "refresh_financials", False),
        refresh_insights=getattr(args, "refresh_insights", False),
        interactive=not args.non_interactive,
    )
