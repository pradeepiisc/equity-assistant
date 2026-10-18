#!/usr/bin/env python3
"""Batch fetch financials for all companies in data/companies/ via Chrome CDP.
Usage: python scripts/batch_fetch_financials.py [--refresh] [--symbols SYM1 SYM2 ...]
"""

import json
import sys
import time
import re
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.financial_fetcher import run as fetch_financials
from llm.client import get_config

COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
WATCHLIST_FILE = PROJECT_ROOT / "watchlist.yaml"

EXPECTED_Q = "Mar 2026"  # target quarter


def _get_latest_quarter(symbol: str) -> str | None:
    """Get the latest quarter from existing financials."""
    fin_dir = COMPANIES_DIR / symbol / "financials"
    if not fin_dir.exists():
        return None
    files = sorted(fin_dir.glob(f"{symbol}_financials_*.txt"), reverse=True)
    if not files:
        return None
    text = files[0].read_text()
    m = re.search(r"## Quarterly Results\s*\n", text)
    if not m:
        return None
    header = text[m.end():].split("\n")[0]
    quarters = [q.strip() for q in header.split("|")[1:] if q.strip()]
    return quarters[-1] if quarters else None


def main():
    import yaml

    refresh = "--refresh" in sys.argv
    only_symbols = []
    if "--symbols" in sys.argv:
        idx = sys.argv.index("--symbols")
        only_symbols = [s.upper() for s in sys.argv[idx + 1:] if not s.startswith("--")]

    # Load watchlist for screener URLs
    wl_map = {}
    if WATCHLIST_FILE.exists():
        with open(WATCHLIST_FILE) as f:
            wl = yaml.safe_load(f) or {}
        for s in wl.get("stocks", []):
            sym = s.get("symbol", "").upper()
            wl_map[sym] = s

    # Build company list from data/companies/
    today = datetime.now().strftime("%Y%m%d")
    companies = []
    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        sym = d.name.upper()
        if only_symbols and sym not in only_symbols:
            continue

        # Check if already fetched today
        fin_dir = d / "financials"
        today_file = fin_dir / f"{sym}_financials_{today}.txt"
        if today_file.exists() and not refresh:
            continue  # already fetched today

        # Build company dict
        meta_file = d / "meta.json"
        meta = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                pass

        screener_url = meta.get("screener_url", "")
        if not screener_url and sym in wl_map:
            screener_url = wl_map[sym].get("screener_url", "")
        if not screener_url:
            screener_url = f"https://www.screener.in/company/{sym}/"

        companies.append({
            "symbol": sym,
            "screener_url": screener_url,
            "name": meta.get("name", sym),
            "isin": meta.get("isin", ""),
            "latest_q": "pending",
        })

    print(f"\n{'=' * 60}")
    print(f"  BATCH FINANCIAL FETCH — Target: {EXPECTED_Q}")
    print(f"{'=' * 60}")
    print(f"  Companies needing update: {len(companies)}")
    if only_symbols:
        print(f"  Filter: {', '.join(only_symbols)}")
    print()

    if not companies:
        print("  All companies already have Mar 2026 data!")
        return

    cfg = get_config()
    success = 0
    failed = 0
    consecutive_fails = 0
    MAX_CONSECUTIVE_FAILS = 10

    for i, c in enumerate(companies, 1):
        sym = c["symbol"]
        print(f"  [{i:3d}/{len(companies)}] {sym:<16} (latest: {c['latest_q']})")

        try:
            result = fetch_financials(c, cfg, refresh=True)
            if result["status"] == "success":
                success += 1
                consecutive_fails = 0
                # Check if we got the target quarter
                new_q = _get_latest_quarter(sym)
                flag = "✓ " + (new_q or "?")
                if new_q == EXPECTED_Q:
                    flag += " 🎯"
                print(f"             {flag}")
            else:
                failed += 1
                consecutive_fails += 1
                print(f"             ✗ {result.get('error', '?')[:60]}")
        except Exception as e:
            failed += 1
            consecutive_fails += 1
            print(f"             ✗ Error: {str(e)[:60]}")

        # Stop on potential IP block
        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
            print(f"\n  ⚠️ {MAX_CONSECUTIVE_FAILS} consecutive failures — possible IP block. Stopping.")
            break

        # Brief pause between requests
        time.sleep(0.5)

    print(f"\n{'=' * 60}")
    print(f"  FETCH COMPLETE")
    print(f"  Success: {success} | Failed: {failed} | Total: {len(companies)}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
