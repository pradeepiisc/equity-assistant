"""
Workflow: Watchlist Monitor
============================
Iterates all companies in watchlist.yaml and runs full analysis for each.

Usage:
    python -m workflows.watchlist_monitor
"""

import sys
import time
from pathlib import Path

import yaml

from workflows.full_company_analysis import run as run_company_analysis


def _load_watchlist() -> list[dict]:
    watchlist_path = Path(__file__).parent.parent / "watchlist.yaml"
    with open(watchlist_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("stocks", [])


def run() -> None:
    """Run full analysis for every company in the watchlist."""
    watchlist = _load_watchlist()

    if not watchlist:
        print("[ERROR] watchlist.yaml is empty or has no stocks defined.")
        return

    print(f"\n{'='*60}")
    print(f"  WATCHLIST MONITOR — {len(watchlist)} companies")
    print(f"{'='*60}\n")

    summary: list[dict] = []

    for i, company in enumerate(watchlist, 1):
        symbol = company.get("symbol", "UNKNOWN")
        name = company.get("name", symbol)

        print(f"\n[{i}/{len(watchlist)}] Starting analysis for {name} ({symbol})")

        start_time = time.time()
        try:
            run_company_analysis(symbol)
            elapsed = time.time() - start_time
            summary.append({"symbol": symbol, "status": "done", "elapsed_s": round(elapsed, 1)})
        except Exception as exc:
            elapsed = time.time() - start_time
            summary.append({"symbol": symbol, "status": "error", "error": str(exc), "elapsed_s": round(elapsed, 1)})
            print(f"[ERROR] {symbol} failed with: {exc}")

    # ── Final Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  WATCHLIST MONITOR — COMPLETION SUMMARY")
    print(f"{'='*60}")
    for entry in summary:
        status_str = "✓" if entry["status"] == "done" else "✗"
        elapsed_str = f"{entry['elapsed_s']}s"
        error_str = f"  → {entry.get('error', '')}" if entry["status"] == "error" else ""
        print(f"  {status_str} {entry['symbol']:<12} {elapsed_str:<8}{error_str}")
    print()

    n_done = sum(1 for e in summary if e["status"] == "done")
    n_error = sum(1 for e in summary if e["status"] == "error")
    print(f"  Completed: {n_done}/{len(watchlist)}  |  Errors: {n_error}")
    print()


if __name__ == "__main__":
    run()
