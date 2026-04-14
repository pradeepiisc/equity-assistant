"""
Workflow: Quarterly Review
===========================
Triggered at results time. Fetches fresh transcripts via concall_fetcher,
then runs full analysis for one or all companies.

Usage:
    python -m workflows.quarterly_review            # all companies in watchlist
    python -m workflows.quarterly_review QPOWER     # single company
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from llm.client import get_config
from skills import concall_fetcher
from workflows.full_company_analysis import run as run_full_analysis


def _load_watchlist() -> list[dict]:
    watchlist_path = Path(__file__).parent.parent / "watchlist.yaml"
    with open(watchlist_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("stocks", [])


def run(symbol: str | None = None) -> None:
    """
    Quarterly review:
    1. Fetch latest concall PDFs via concall_fetcher
    2. Run full_company_analysis

    If symbol is None, runs for all companies in watchlist.
    """
    config = get_config()
    watchlist = _load_watchlist()

    if symbol:
        companies = [c for c in watchlist if c.get("symbol", "").upper() == symbol.upper()]
        if not companies:
            print(f"[ERROR] Symbol '{symbol}' not found in watchlist.yaml.")
            return
    else:
        companies = watchlist

    print(f"\n{'='*60}")
    print(f"  QUARTERLY REVIEW — {len(companies)} company/ies")
    print(f"{'='*60}\n")

    for company in companies:
        sym = company["symbol"]
        name = company.get("name", sym)

        print(f"\n── {name} ({sym}) ──────────────────────────────────────")

        # Step 1: fetch latest transcripts
        print(f"[{sym}] Fetching latest concall PDFs...")
        fetch_result = concall_fetcher.run(company, config)

        n_new = len(fetch_result.get("data", {}).get("downloaded", []))
        n_skip = len(fetch_result.get("data", {}).get("skipped", []))
        print(f"[{sym}] Fetch done: {n_new} new PDFs, {n_skip} already present.")

        # Step 2: run full analysis
        print(f"[{sym}] Running full analysis...")
        run_full_analysis(sym)


if __name__ == "__main__":
    target = sys.argv[1].upper() if len(sys.argv) > 1 else None
    run(target)
