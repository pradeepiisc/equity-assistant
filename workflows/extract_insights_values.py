"""
Workflow: Extract Insights Values
==================================
Batch-runs the insights_values skill for all (or selected) portfolio companies.
Extracts Screener Insights metric values from transcripts/PPTs via LLM and
saves a quarterly table to data/companies/{SYMBOL}/insights/insights_values.md

Usage:
    python -m workflows.extract_insights_values [--symbols S1 S2 ...] [--refresh]
                                                [--batch-size N] [--offset N]
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from llm.client import get_config
from skills.insights_values import run as _insights_values_run

PROJECT_ROOT = Path(__file__).parent.parent


def _load_all_companies() -> list[dict]:
    companies: list[dict] = []
    seen: set[str] = set()
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p) as f:
            data = yaml.safe_load(f)
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in seen:
                seen.add(sym)
                companies.append(c)
    return companies


def run(
    symbols: list[str] | None = None,
    refresh: bool = False,
    batch_size: int | None = None,
    offset: int = 0,
) -> None:
    config = get_config()
    all_companies = _load_all_companies()

    if symbols:
        companies = [c for c in all_companies if c.get("symbol", "").upper() in symbols]
    else:
        companies = all_companies[offset:]
        if batch_size:
            companies = companies[:batch_size]

    total = len(companies)
    print(f"\n{'='*60}")
    print(f"  INSIGHTS VALUES EXTRACTION")
    print(f"  Companies: {total}  |  Refresh: {refresh}")
    print(f"{'='*60}\n")

    t0 = datetime.now()
    counts = {"done": 0, "skipped": 0, "no_data": 0, "error": 0}

    for i, company in enumerate(companies, 1):
        sym = company.get("symbol", "").upper()
        print(f"[{i}/{total}] {sym}")

        result = _insights_values_run(company, config, refresh=refresh)
        status = result.get("status", "error")

        if status == "success":
            counts["done"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        elif status == "no_data":
            counts["no_data"] += 1
        else:
            counts["error"] += 1

        time.sleep(0.2)  # gentle pacing between LLM calls

    elapsed = (datetime.now() - t0).seconds // 60
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Done      : {counts['done']}")
    print(f"  Skipped   : {counts['skipped']}")
    print(f"  No data   : {counts['no_data']}")
    print(f"  Errors    : {counts['error']}")
    print(f"  Time      : {elapsed} min")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    symbols_arg: list[str] | None = None
    do_refresh = "--refresh" in sys.argv
    bsize: int | None = None
    off = 0

    if "--symbols" in sys.argv:
        idx = sys.argv.index("--symbols")
        symbols_arg = [s.upper() for s in sys.argv[idx + 1:] if not s.startswith("--")]

    if "--batch-size" in sys.argv:
        idx = sys.argv.index("--batch-size")
        bsize = int(sys.argv[idx + 1])

    if "--offset" in sys.argv:
        idx = sys.argv.index("--offset")
        off = int(sys.argv[idx + 1])

    run(symbols=symbols_arg, refresh=do_refresh, batch_size=bsize, offset=off)
