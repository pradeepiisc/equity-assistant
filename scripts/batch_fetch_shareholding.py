#!/usr/bin/env python3
"""Batch fetch shareholding for all companies in data/companies/ via Chrome CDP.

Usage:
    python scripts/batch_fetch_shareholding.py                     # skip already-fetched-today
    python scripts/batch_fetch_shareholding.py --refresh           # re-fetch even if today's file exists
    python scripts/batch_fetch_shareholding.py --symbols SYM1 SYM2 # specific symbols only
    python scripts/batch_fetch_shareholding.py --check-quarter "Mar 2026"  # only fetch if missing this quarter
"""

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.shareholding_fetcher import run as fetch_shareholding
from llm.client import get_config

COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
WATCHLIST_FILE = PROJECT_ROOT / "watchlist.yaml"


def _get_latest_sh_quarter(symbol: str) -> str | None:
    """Get the latest quarter from the most recent shareholding file."""
    sh_dir = COMPANIES_DIR / symbol / "shareholding"
    if not sh_dir.exists():
        return None
    files = sorted(sh_dir.glob(f"{symbol}_shareholding_*.txt"), reverse=True)
    if not files:
        return None
    text = files[0].read_text()[:500]
    # Look for "Quarter: Mar 2026" pattern or extract from Quarters Covered
    m = re.search(r"Quarter:\s*(\w+ \d{4})", text)
    if m:
        return m.group(1)
    # Try extracting from "Quarters Covered" line
    m2 = re.search(r"## Quarters Covered\n(.*)", text)
    if m2:
        quarters = [q.strip() for q in m2.group(1).split(",") if q.strip()]
        return quarters[-1] if quarters else None
    return None


def main():
    import yaml

    refresh = "--refresh" in sys.argv
    check_quarter = None
    if "--check-quarter" in sys.argv:
        idx = sys.argv.index("--check-quarter")
        if idx + 1 < len(sys.argv):
            check_quarter = sys.argv[idx + 1]

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

    # Build company list
    today = datetime.now().strftime("%Y%m%d")
    companies = []
    already_have = 0

    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        sym = d.name.upper()
        if only_symbols and sym not in only_symbols:
            continue

        # Check if already fetched today
        sh_dir = d / "shareholding"
        today_file = sh_dir / f"{sym}_shareholding_{today}.txt"
        if today_file.exists() and not refresh:
            # If check_quarter specified, verify it's present
            if check_quarter:
                text = today_file.read_text()[:500]
                if check_quarter in text:
                    already_have += 1
                    continue
                # Has today's file but missing target quarter — re-fetch won't help
                # (Screener may not have it yet)
                already_have += 1
                continue
            else:
                already_have += 1
                continue

        # If check_quarter, skip if already have that quarter in any file
        if check_quarter and not refresh:
            latest_q = _get_latest_sh_quarter(sym)
            if latest_q == check_quarter:
                already_have += 1
                continue

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
        })

    print(f"\n{'=' * 60}")
    print(f"  BATCH SHAREHOLDING FETCH")
    if check_quarter:
        print(f"  Target quarter: {check_quarter}")
    print(f"{'=' * 60}")
    print(f"  Companies needing update: {len(companies)}")
    print(f"  Already up to date:       {already_have}")
    if only_symbols:
        print(f"  Filter: {', '.join(only_symbols)}")
    print()

    if not companies:
        print("  All companies already up to date!")
        return

    cfg = get_config()
    success = 0
    failed = 0
    consecutive_fails = 0
    MAX_CONSECUTIVE_FAILS = 10

    for i, c in enumerate(companies, 1):
        sym = c["symbol"]
        print(f"  [{i:3d}/{len(companies)}] {sym:<16}", end="", flush=True)

        try:
            result = fetch_shareholding(c, cfg)
            if result["status"] == "success":
                success += 1
                consecutive_fails = 0
                q = _get_latest_sh_quarter(sym)
                flag = f"✓ {q or '?'}"
                if check_quarter and q == check_quarter:
                    flag += " 🎯"
                print(f"  {flag}")
            elif result["status"] == "no_data":
                failed += 1
                consecutive_fails += 1
                print(f"  ✗ no shareholding data")
            else:
                failed += 1
                consecutive_fails += 1
                print(f"  ✗ {result.get('error', '?')[:60]}")
        except Exception as e:
            failed += 1
            consecutive_fails += 1
            print(f"  ✗ Error: {str(e)[:60]}")

        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
            print(f"\n  ⚠️ {MAX_CONSECUTIVE_FAILS} consecutive failures — possible issue. Stopping.")
            break

        # Brief pause between requests
        time.sleep(1.0)

    print(f"\n{'=' * 60}")
    print(f"  FETCH COMPLETE")
    print(f"  Success: {success} | Failed: {failed} | Total: {len(companies)}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
