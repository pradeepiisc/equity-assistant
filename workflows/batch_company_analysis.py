"""
Workflow: Batch Company Analysis
==================================
Runs data_fetch + full_company_analysis for all (or selected) companies
in watchlist.yaml.

Steps per company:
  1. data_fetch   — transcripts, news, shareholding, financials
  2. full_company_analysis — transcript + skills → master investment report

Usage:
    python -m workflows.batch_company_analysis                   # all watchlist
    python -m workflows.batch_company_analysis --all             # all companies in data/companies/
    python -m workflows.batch_company_analysis --symbols ZENTEC DCAL
    python -m workflows.batch_company_analysis --skip-existing   # skip if master_report.md exists
    python -m workflows.batch_company_analysis --data-only       # fetch data, no LLM analysis
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import yaml

from llm.client import get_config

PROJECT_ROOT = Path(__file__).parent.parent


def _load_watchlist() -> list[dict]:
    # Import full_company_analysis's loader to get both watchlist and portfolio companies
    from workflows.full_company_analysis import _load_all_companies
    return _load_all_companies()


def _get_all_symbols_with_data() -> list[str]:
    """Get all symbols that have a directory in data/companies."""
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    if not data_root.exists():
        return []
    
    symbols = []
    for item in data_root.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            # Basic validation: symbols are usually uppercase or numeric
            if item.name.isupper() or item.name.isdigit():
                symbols.append(item.name)
    return sorted(symbols)


def _report_exists(symbol: str) -> bool:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    report = data_root / symbol / "reports" / "master_report.md"
    return report.exists()


def run(
    symbols: list[str] | None = None,
    all_data: bool = False,
    skip_existing: bool = False,
    data_only: bool = False,
    analysis_only: bool = False,
    transcript_limit: int = 4,
    news_limit: int = 5,
) -> None:
    from workflows import data_fetch, full_company_analysis  # noqa: PLC0415

    all_known_companies = _load_watchlist()
    companies_map = {c["symbol"].upper(): c for c in all_known_companies if "symbol" in c}
    
    if symbols:
        syms_to_process = [s.upper() for s in symbols]
    elif all_data:
        syms_to_process = _get_all_symbols_with_data()
        print(f"Found {len(syms_to_process)} symbols in data/companies/")
    else:
        # Default to watchlist only (legacy behavior)
        wl_path = PROJECT_ROOT / "watchlist.yaml"
        with open(wl_path) as f:
            wl_data = yaml.safe_load(f) or {}
        syms_to_process = [c["symbol"].upper() for c in wl_data.get("stocks", []) if "symbol" in c]

    companies = []
    for sym in syms_to_process:
        if sym in companies_map:
            companies.append(companies_map[sym])
        else:
            # Create a minimal company dict if we have the folder but not the metadata
            companies.append({"symbol": sym, "name": sym, "sector": "Unknown"})

    total = len(companies)
    print(f"\n{'='*60}")
    print(f"  BATCH COMPANY ANALYSIS  —  {total} companies")
    print(f"{'='*60}")
    if skip_existing:
        print("  Mode: skip companies with existing master_report.md")
    if data_only:
        print("  Mode: data fetch only (no LLM analysis)")
    if analysis_only:
        print("  Mode: analysis only (skip data fetch)")
    print()

    results: list[dict] = []

    for idx, company in enumerate(companies, 1):
        sym = company.get("symbol", "").upper()
        name = company.get("name", sym)

        print(f"\n[{idx}/{total}] {'─'*55}")
        print(f"  {sym}  —  {name}")
        print(f"{'─'*60}")

        status = {"symbol": sym, "data_fetch": "skipped", "analysis": "skipped"}

        # ── Skip check ────────────────────────────────────────────────────
        if skip_existing and _report_exists(sym):
            print(f"  → master_report.md exists — skipping.")
            status["data_fetch"] = "existing"
            status["analysis"] = "existing"
            results.append(status)
            continue

        # ── Step 1: Data Fetch ─────────────────────────────────────────────
        if not analysis_only:
            try:
                fetch_results = data_fetch.run(
                    symbol=sym,
                    fetch_transcripts=True,
                    fetch_news=True,
                    fetch_shareholding=True,
                    fetch_financials=True,
                    transcript_limit=transcript_limit,
                    news_limit=news_limit,
                )
                any_fail = any(
                    r.get("status") == "error" for r in fetch_results.values()
                )
                status["data_fetch"] = "error" if any_fail else "done"
            except Exception as exc:
                print(f"  [data_fetch ERROR] {exc}")
                traceback.print_exc()
                status["data_fetch"] = "error"

        # ── Step 2: Full Analysis ──────────────────────────────────────────
        if not data_only:
            try:
                full_company_analysis.run(sym)
                # Verify report was actually created (run() returns early without raising on abort)
                if _report_exists(sym):
                    status["analysis"] = "done"
                else:
                    status["analysis"] = "aborted"
            except Exception as exc:
                print(f"  [full_company_analysis ERROR] {exc}")
                traceback.print_exc()
                status["analysis"] = "error"

        results.append(status)

    # ── Final Summary ──────────────────────────────────────────────────────
    print(f"\n\n{'='*60}")
    print(f"  BATCH COMPLETE  —  {total} companies")
    print(f"{'='*60}")
    print(f"  {'SYMBOL':<12}  {'DATA FETCH':<14}  ANALYSIS")
    print(f"  {'-'*12}  {'-'*14}  {'-'*14}")
    for r in results:
        df_icon = "✓" if r["data_fetch"] == "done" else ("–" if r["data_fetch"] in ("skipped", "existing") else "✗")
        an_icon = "✓" if r["analysis"] == "done" else ("–" if r["analysis"] in ("skipped", "existing") else "✗")
        print(f"  {r['symbol']:<12}  {df_icon} {r['data_fetch']:<13}  {an_icon} {r['analysis']}")

    done = sum(1 for r in results if r["analysis"] == "done")
    errors = sum(1 for r in results if r["analysis"] == "error")
    skipped = total - done - errors
    print(f"\n  Done: {done}  Errors: {errors}  Skipped/Existing: {skipped}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Batch data fetch + investment analysis for all watchlist companies"
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="Process only these symbols (default: all watchlist)"
    )
    parser.add_argument(
        "--all", action="store_true", dest="all_data",
        help="Process all symbols found in data/companies/ folder"
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip companies that already have a master_report.md"
    )
    parser.add_argument(
        "--data-only", action="store_true",
        help="Only run data_fetch, skip full_company_analysis"
    )
    parser.add_argument(
        "--analysis-only", action="store_true",
        help="Skip data_fetch, only run full_company_analysis"
    )
    parser.add_argument(
        "--transcript-limit", type=int, default=4,
        help="Max concall PDFs per company (default 4)"
    )
    parser.add_argument(
        "--news-limit", type=int, default=5,
        help="Max news articles per company (default 5)"
    )
    args = parser.parse_args()

    run(
        symbols=args.symbols,
        all_data=args.all_data,
        skip_existing=args.skip_existing,
        data_only=args.data_only,
        analysis_only=args.analysis_only,
        transcript_limit=args.transcript_limit,
        news_limit=args.news_limit,
    )
