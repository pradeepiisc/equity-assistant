#!/usr/bin/env python3
"""
Batch Data Fetch + Analysis
============================
Fetches data and runs analysis for new/all companies in phases:

Phase 1: data_fetch for all new companies (no data yet)
Phase 2: data_fetch --refresh for all existing companies
Phase 3: Download quarterly results PDFs for specific companies (one-time)
Phase 4: Run full_company_analysis for all companies that got new data

Usage:
    python -m scripts.batch_data_fetch                     # Phase 1 only (new companies)
    python -m scripts.batch_data_fetch --all               # Phase 1 + 2 (new + refresh all)
    python -m scripts.batch_data_fetch --results-only      # Phase 3 only (specific results PDFs)
    python -m scripts.batch_data_fetch --analyze           # Phase 4 after data fetch
    python -m scripts.batch_data_fetch --all --analyze     # Full pipeline
    python -m scripts.batch_data_fetch --symbols ZAGGLE,KAYNES  # Specific symbols only
    python -m scripts.batch_data_fetch --friday-results    # Fetch data + results PDFs for Friday companies
"""

import argparse
import json
import sys
import time
import traceback
import yaml
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.cdp_helper import is_available as cdp_available, ensure_chrome_running

DELAY_BETWEEN_COMPANIES = 5  # seconds between companies to avoid IP blocking
DELAY_BETWEEN_CDP_OPS = 2    # seconds between CDP operations

# Companies whose Q4 FY26 results were declared on Friday 30-May-2026
# and whose Screener financials won't be updated yet
FRIDAY_RESULTS_COMPANIES = [
    "BALUFORGE", "PPL", "ICIL", "AWHCL",
    "GUJAPOLLO", "SUBAHOTELS", "TECHERA", "TBI-SM",
]


def _load_all_companies() -> list[dict]:
    """Load all companies from both YAML files."""
    companies = []
    seen = set()
    for yf in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yf
        if not p.exists():
            continue
        data = yaml.safe_load(p.read_text()) or {}
        for s in data.get("stocks", []):
            sym = s.get("symbol", "").upper().strip()
            if sym and sym not in seen:
                seen.add(sym)
                companies.append(s)
    return companies


def _find_new_companies(companies: list[dict]) -> list[dict]:
    """Find companies that have folder but no financials or transcripts."""
    data_dir = PROJECT_ROOT / "data" / "companies"
    new = []
    for c in companies:
        sym = c.get("symbol", "").upper()
        d = data_dir / sym
        if not d.exists():
            new.append(c)
            continue
        fin_dir = d / "financials"
        has_fin = fin_dir.exists() and len(list(fin_dir.glob("*.txt"))) > 0
        tr_dir = d / "transcripts"
        has_tr = tr_dir.exists() and len(list(tr_dir.glob("*.pdf"))) > 0
        if not has_fin and not has_tr:
            new.append(c)
    return new


def _find_companies_without_reports(companies: list[dict]) -> list[dict]:
    """Find companies that have data but no reports."""
    data_dir = PROJECT_ROOT / "data" / "companies"
    no_rep = []
    for c in companies:
        sym = c.get("symbol", "").upper()
        d = data_dir / sym
        if not d.exists():
            continue
        rep_dir = d / "reports"
        has_rep = rep_dir.exists() and len(list(rep_dir.iterdir())) > 0
        if not has_rep:
            # But should have some data
            fin_dir = d / "financials"
            has_fin = fin_dir.exists() and len(list(fin_dir.glob("*.txt"))) > 0
            if has_fin:
                no_rep.append(c)
    return no_rep


def phase1_fetch_new(companies: list[dict], log_file: Path) -> dict:
    """Phase 1: Fetch data for all new companies."""
    from workflows.data_fetch import run as data_fetch_run

    results = {"success": [], "failed": [], "skipped": []}
    total = len(companies)

    print(f"\n{'='*60}")
    print(f"  PHASE 1: DATA FETCH FOR {total} NEW COMPANIES")
    print(f"{'='*60}\n")

    for i, c in enumerate(companies, 1):
        sym = c.get("symbol", "").upper()
        name = c.get("name", sym)
        print(f"\n[{i}/{total}] {sym} — {name}")
        print(f"{'─'*50}")

        try:
            r = data_fetch_run(
                symbol=sym,
                fetch_transcripts=True,
                fetch_news=True,
                fetch_shareholding=True,
                fetch_financials=True,
                fetch_insights=True,
                fetch_valuepickr=True,
                transcript_limit=4,
                news_limit=5,
                interactive=False,
            )
            # Check if any step succeeded
            any_success = any(
                v.get("status") in ("success", "skipped")
                for v in r.values()
                if isinstance(v, dict)
            )
            if any_success:
                results["success"].append(sym)
                _log(log_file, f"[OK] {sym}")
            else:
                results["failed"].append(sym)
                _log(log_file, f"[FAIL] {sym} — no successful steps")

        except Exception as e:
            print(f"  [!] {sym} failed: {e}")
            traceback.print_exc()
            results["failed"].append(sym)
            _log(log_file, f"[ERR] {sym} — {e}")

        if i < total:
            print(f"  ... waiting {DELAY_BETWEEN_COMPANIES}s before next ...")
            time.sleep(DELAY_BETWEEN_COMPANIES)

    return results


def phase2_refresh_all(companies: list[dict], log_file: Path) -> dict:
    """Phase 2: Refresh financials for all existing companies."""
    from workflows.data_fetch import run as data_fetch_run

    results = {"success": [], "failed": [], "skipped": []}
    total = len(companies)

    print(f"\n{'='*60}")
    print(f"  PHASE 2: REFRESH FINANCIALS FOR {total} COMPANIES")
    print(f"{'='*60}\n")

    for i, c in enumerate(companies, 1):
        sym = c.get("symbol", "").upper()
        name = c.get("name", sym)
        print(f"\n[{i}/{total}] {sym} — {name}")

        try:
            r = data_fetch_run(
                symbol=sym,
                fetch_transcripts=False,
                fetch_news=False,
                fetch_shareholding=False,
                fetch_financials=True,
                fetch_insights=True,
                fetch_valuepickr=False,
                refresh_financials=True,
                refresh_insights=True,
                interactive=False,
            )
            fin_status = r.get("financials", {}).get("status", "unknown")
            if fin_status in ("success", "skipped"):
                results["success"].append(sym)
                _log(log_file, f"[OK] {sym} refresh")
            else:
                results["failed"].append(sym)
                _log(log_file, f"[FAIL] {sym} refresh — {fin_status}")

        except Exception as e:
            results["failed"].append(sym)
            _log(log_file, f"[ERR] {sym} refresh — {e}")

        if i < total:
            time.sleep(DELAY_BETWEEN_CDP_OPS)

    return results


def phase3_friday_results(symbols: list[str], log_file: Path) -> dict:
    """Phase 3: Fetch fresh data for companies with Friday results."""
    from workflows.data_fetch import run as data_fetch_run

    results = {"success": [], "failed": []}
    total = len(symbols)

    print(f"\n{'='*60}")
    print(f"  PHASE 3: FRIDAY RESULTS — {total} COMPANIES")
    print(f"  Refreshing financials + transcripts + insights")
    print(f"{'='*60}\n")

    for i, sym in enumerate(symbols, 1):
        sym = sym.upper()
        print(f"\n[{i}/{total}] {sym}")
        print(f"{'─'*50}")

        try:
            r = data_fetch_run(
                symbol=sym,
                fetch_transcripts=True,
                fetch_news=True,
                fetch_shareholding=False,
                fetch_financials=True,
                fetch_insights=True,
                fetch_valuepickr=False,
                refresh_financials=True,
                refresh_insights=True,
                transcript_limit=4,
                interactive=False,
            )
            results["success"].append(sym)
            _log(log_file, f"[OK] {sym} friday-results")
        except Exception as e:
            results["failed"].append(sym)
            _log(log_file, f"[ERR] {sym} friday-results — {e}")

        if i < total:
            time.sleep(DELAY_BETWEEN_COMPANIES)

    return results


def phase4_analyze(symbols: list[str], log_file: Path) -> dict:
    """Phase 4: Run full_company_analysis for given symbols."""
    from workflows.full_company_analysis import run as run_analysis

    results = {"success": [], "failed": []}
    total = len(symbols)

    print(f"\n{'='*60}")
    print(f"  PHASE 4: FULL ANALYSIS FOR {total} COMPANIES")
    print(f"{'='*60}\n")

    for i, sym in enumerate(symbols, 1):
        sym = sym.upper()
        print(f"\n[{i}/{total}] Analyzing {sym}...")
        print(f"{'─'*50}")

        try:
            run_analysis(sym)
            results["success"].append(sym)
            _log(log_file, f"[OK] {sym} analysis")
        except Exception as e:
            print(f"  [!] Analysis failed for {sym}: {e}")
            results["failed"].append(sym)
            _log(log_file, f"[ERR] {sym} analysis — {e}")

    return results


def _log(log_file: Path, msg: str):
    """Append a timestamped line to the log file."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"[{ts}] {msg}\n")


def main():
    parser = argparse.ArgumentParser(description="Batch data fetch + analysis")
    parser.add_argument("--all", action="store_true", help="Fetch new + refresh all existing")
    parser.add_argument("--refresh-only", action="store_true", help="Only refresh existing companies")
    parser.add_argument("--friday-results", action="store_true", help="Fetch data for Friday results companies")
    parser.add_argument("--analyze", action="store_true", help="Run full_company_analysis after fetch")
    parser.add_argument("--analyze-only", action="store_true", help="Only run analysis (no fetch)")
    parser.add_argument("--symbols", type=str, help="Comma-separated specific symbols")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    log_file = PROJECT_ROOT / f"batch_fetch_{date.today().isoformat()}.log"
    _log(log_file, "=== BATCH DATA FETCH STARTED ===")

    # Check CDP
    if not args.dry_run and not args.analyze_only:
        if not ensure_chrome_running():
            print("\n❌ Chrome CDP not available. Please run:")
            print("   bash scripts/launch_chrome_debug.sh")
            print("   Then re-run this script.")
            sys.exit(1)
        print("✅ Chrome CDP available")

    all_companies = _load_all_companies()
    new_companies = _find_new_companies(all_companies)

    # Determine target symbols
    if args.symbols:
        target_syms = [s.strip().upper() for s in args.symbols.split(",")]
        target_companies = [c for c in all_companies if c.get("symbol", "").upper() in target_syms]
    elif args.refresh_only:
        # Only existing companies (not new)
        new_syms = {c.get("symbol", "").upper() for c in new_companies}
        target_companies = [c for c in all_companies if c.get("symbol", "").upper() not in new_syms]
    else:
        target_companies = all_companies

    if args.dry_run:
        print(f"\n📋 DRY RUN — would process:")
        print(f"  Total companies: {len(all_companies)}")
        print(f"  New (no data):   {len(new_companies)}")
        if args.symbols:
            print(f"  Target symbols:  {', '.join(target_syms)}")
        if args.friday_results:
            print(f"  Friday results:  {', '.join(FRIDAY_RESULTS_COMPANIES)}")
        print(f"\n  New companies: {', '.join(c.get('symbol','') for c in new_companies)}")
        return

    all_fetched = []

    # Phase 1: New companies
    if not args.refresh_only and not args.analyze_only and not args.friday_results:
        if new_companies and not args.symbols:
            r1 = phase1_fetch_new(new_companies, log_file)
            all_fetched.extend(r1["success"])
            print(f"\n✅ Phase 1 done: {len(r1['success'])} ok, {len(r1['failed'])} failed")
            if r1["failed"]:
                print(f"  Failed: {', '.join(r1['failed'])}")
        elif args.symbols:
            # Fetch specific symbols
            r1 = phase1_fetch_new(target_companies, log_file)
            all_fetched.extend(r1["success"])
        else:
            print("✅ No new companies to fetch.")

    # Phase 2: Refresh all existing
    if (args.all or args.refresh_only) and not args.analyze_only:
        if args.refresh_only:
            refresh_targets = target_companies
        else:
            new_syms = {c.get("symbol", "").upper() for c in new_companies}
            refresh_targets = [c for c in all_companies if c.get("symbol", "").upper() not in new_syms]
        r2 = phase2_refresh_all(refresh_targets, log_file)
        all_fetched.extend(r2["success"])
        print(f"\n✅ Phase 2 done: {len(r2['success'])} ok, {len(r2['failed'])} failed")

    # Phase 3: Friday results companies
    if args.friday_results:
        r3 = phase3_friday_results(FRIDAY_RESULTS_COMPANIES, log_file)
        all_fetched.extend(r3["success"])
        print(f"\n✅ Phase 3 done: {len(r3['success'])} ok, {len(r3['failed'])} failed")

    # Phase 4: Run analysis
    if args.analyze or args.analyze_only:
        if args.analyze_only and args.symbols:
            analyze_syms = [s.strip().upper() for s in args.symbols.split(",")]
        elif args.analyze_only:
            # Analyze all companies with data but no reports
            no_rep = _find_companies_without_reports(all_companies)
            analyze_syms = [c.get("symbol", "").upper() for c in no_rep] + all_fetched
        else:
            analyze_syms = list(set(all_fetched))

        if analyze_syms:
            r4 = phase4_analyze(list(set(analyze_syms)), log_file)
            print(f"\n✅ Phase 4 done: {len(r4['success'])} ok, {len(r4['failed'])} failed")
        else:
            print("✅ No companies to analyze.")

    _log(log_file, "=== BATCH DATA FETCH COMPLETED ===")
    print(f"\n📝 Log: {log_file}")


if __name__ == "__main__":
    main()
