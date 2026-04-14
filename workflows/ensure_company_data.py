"""
Workflow: Ensure Company Data
==============================
Ensures every company in the portfolio and watchlist has:
  1. The correct folder structure:
       data/companies/{SYMBOL}/
           financials/  transcripts/  ppt/  insights/
           shareholding/  news/  reports/

  2. Screener Insights fetched and saved.

  3. Optionally: run full data fetch (transcripts, PPTs, financials, news, shareholding).

Usage:
    # Create all folder structures only (fast)
    python -m workflows.ensure_company_data --folders-only

    # Create folders + fetch insights only (moderate)
    python -m workflows.ensure_company_data --insights-only

    # Fetch transcripts + PPTs only for all companies (no Chrome CDP needed)
    python -m workflows.ensure_company_data --concalls-only

    # Full data fetch for specific symbols
    python -m workflows.ensure_company_data --symbols DCAL BETA --full

    # Full data fetch for all companies
    python -m workflows.ensure_company_data --full

    # Just list which companies are missing folders
    python -m workflows.ensure_company_data --dry-run
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import yaml
from pathlib import Path

from llm.client import get_config
from skills import concall_fetcher, financial_fetcher, insights_fetcher, shareholding_fetcher
from skills.cdp_helper import is_available as _chrome_available

PROJECT_ROOT = Path(__file__).parent.parent

REQUIRED_SUBFOLDERS = ["financials", "transcripts", "ppt", "insights", "shareholding", "news", "reports"]

# Portfolio symbols that are ETFs — no Screener data, skip Screener steps
ETF_SYMBOLS = {"GOLDIETF", "SILVERIETF", "NIFTYBEES", "JUNIORBEES", "LIQUIDBEES"}

# ── Screener rate-limit / block detection ─────────────────────────────────────
# These error signatures indicate Screener's CDN has blocked the IP.
# If seen, the batch MUST stop immediately.
_BLOCK_SIGNALS = (
    "SSL",
    "ssl",
    "SSLError",
    "ConnectError",
    "ConnectionError",
    "UNEXPECTED_EOF",
    "handshake",
    "connection refused",
    "Connection refused",
)

# Delay between companies: random in [MIN, MAX] seconds.
_INTER_COMPANY_DELAY_MIN = 8   # seconds
_INTER_COMPANY_DELAY_MAX = 13  # seconds

# Delay between Screener page fetches within the same company.
# 3 pages per company (financials, shareholding, insights) — pace them too.
_INTER_STEP_DELAY_MIN = 3   # seconds
_INTER_STEP_DELAY_MAX = 6   # seconds


def _inter_company_delay() -> None:
    """Sleep a random interval between companies to avoid rate limiting."""
    secs = random.uniform(_INTER_COMPANY_DELAY_MIN, _INTER_COMPANY_DELAY_MAX)
    time.sleep(secs)


def _inter_step_delay() -> None:
    """Brief pause between Screener page fetches for the same company."""
    secs = random.uniform(_INTER_STEP_DELAY_MIN, _INTER_STEP_DELAY_MAX)
    time.sleep(secs)


def _needs_fetch(company_dir: Path, data_type: str, min_pdfs: int = 4) -> bool:
    """
    Return True if this data type still needs to be fetched.

    data_type: 'insights' | 'financials' | 'shareholding' | 'transcripts' | 'ppt'
    """
    if data_type == "insights":
        return not any(company_dir.glob("insights/*_insights_*.txt"))
    if data_type == "financials":
        return not any(company_dir.glob("financials/*_financials_*.txt"))
    if data_type == "shareholding":
        return not any(company_dir.glob("shareholding/*_shareholding_*.txt"))
    if data_type == "transcripts":
        return len(list(company_dir.glob("transcripts/*.pdf"))) < min_pdfs
    if data_type == "ppt":
        return len(list(company_dir.glob("ppt/*.pdf"))) < min_pdfs
    return True


def _looks_like_block(error_str: str) -> bool:
    """Return True if the error string suggests an IP-level CDN block."""
    return any(sig in error_str for sig in _BLOCK_SIGNALS)


def _load_all_companies() -> list[dict]:
    """Load all companies from watchlist.yaml and portfolio_companies.yaml, deduplicated."""
    all_companies: dict[str, dict] = {}  # keyed by cleaned screener_symbol

    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        yaml_path = PROJECT_ROOT / yaml_file
        if not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        for c in data.get("stocks", []):
            sym = str(c.get("screener_symbol") or c.get("symbol", ""))
            sym = sym.upper()
            if sym and sym not in all_companies:
                all_companies[sym] = c

    return list(all_companies.values())


def _get_data_root() -> Path:
    config = get_config()
    return PROJECT_ROOT / config["paths"]["data_root"]


def ensure_folders(symbol: str, data_root: Path, dry_run: bool = False) -> dict:
    """
    Create all required subfolders under data_root/{symbol}/.
    Returns dict with 'created' and 'existing' folder lists.
    """
    company_dir = data_root / symbol
    created = []
    existing = []

    for subfolder in REQUIRED_SUBFOLDERS:
        folder_path = company_dir / subfolder
        if folder_path.exists():
            existing.append(subfolder)
        else:
            if not dry_run:
                folder_path.mkdir(parents=True, exist_ok=True)
            created.append(subfolder)

    return {"symbol": symbol, "created": created, "existing": existing}


def run(
    symbols: list[str] | None = None,
    folders_only: bool = False,
    insights_only: bool = False,
    concalls_only: bool = False,
    full: bool = False,
    dry_run: bool = False,
    refresh_insights: bool = False,
    transcript_limit: int = 999,
    batch_size: int | None = None,
    offset: int = 0,
) -> dict:
    """
    Main entry point.

    Args:
        symbols:          Specific symbols to process (None = all)
        folders_only:     Only create folder structure
        insights_only:    Create folders + fetch insights only
        concalls_only:    Fetch transcripts + PPTs only (no Chrome CDP needed)
        full:             Create folders + full data fetch (transcripts, PPTs, news, financials, insights)
        dry_run:          Report what would be done without writing
        refresh_insights: Force re-fetch insights even if already fetched today
        transcript_limit: Max transcripts/PPTs to fetch per company (default 999 = all)
        batch_size:       Max companies to process per run (None = all). Use to split large batches.
    """
    config = get_config()
    data_root = _get_data_root()
    all_companies = _load_all_companies()

    # Filter to requested symbols
    if symbols:
        requested = {s.upper() for s in symbols}
        companies = [
            c for c in all_companies
            if c.get("symbol", "").upper() in requested
            or str(c.get("screener_symbol") or "").upper() in requested
        ]
        matched = {
            sym
            for c in companies
            for sym in [c.get("symbol", "").upper(), str(c.get("screener_symbol") or "").upper()]
            if sym
        }
        missing = requested - matched
        if missing:
            print(f"[WARN] Symbols not found in any YAML: {', '.join(sorted(missing))}")
    else:
        companies = all_companies

    # ── Apply offset + batch_size limit ──────────────────────────────────
    if offset:
        companies = companies[offset:]
        print(f"[INFO] Starting from offset {offset} ({len(companies)} remaining).")
    if batch_size and len(companies) > batch_size:
        total_remaining = len(companies)
        companies = companies[:batch_size]
        next_offset = offset + batch_size
        print(f"[INFO] Batch {batch_size} of {total_remaining} companies (offset={offset}).")
        print(f"[INFO] Next batch: add --offset {next_offset} to continue.")

    # ── Pre-flight: Chrome must be running for any Screener fetch ────────────
    # concalls_only uses httpx fallback — Chrome not required
    needs_screener = not folders_only and not dry_run and not concalls_only
    if needs_screener and not _chrome_available():
        print()
        print("  ✗  CHROME CDP NOT AVAILABLE — ABORTING")
        print()
        print("  Screener fetches require Chrome running with remote debugging.")
        print("  DO NOT use httpx bulk requests — they cause IP bans within minutes.")
        print()
        print("  Fix:")
        print("    1. Open Terminal.app  (NOT the Windsurf terminal)")
        print("    2. Run: bash scripts/launch_chrome_debug.sh")
        print("    3. Wait for: ✓ Chrome debug session ready at http://127.0.0.1:9222")
        print("    4. Re-run this command")
        print()
        sys.exit(1)

    print(f"\n{'=' * 65}")
    print(f"  ENSURE COMPANY DATA  —  {len(companies)} companies")
    print(f"  Mode: {'dry-run' if dry_run else 'folders-only' if folders_only else 'insights-only' if insights_only else 'concalls-only' if concalls_only else 'full' if full else 'folders+insights'}")
    if needs_screener:
        print(f"  Chrome CDP: {'✓ available' if _chrome_available() else '✗ NOT available'}")
    print(f"  Delay between companies: {_INTER_COMPANY_DELAY_MIN}–{_INTER_COMPANY_DELAY_MAX}s (random)")
    print(f"{'=' * 65}\n")

    summary = {
        "total": len(companies),
        "folders_created": 0,
        "insights_fetched": 0,
        "insights_no_data": 0,
        "insights_errors": 0,
        "financials_fetched": 0,
        "shareholding_fetched": 0,
        "concalls_fetched": 0,
        "skipped_etf": 0,
        "blocked": False,
        "errors": [],
    }

    for i, company in enumerate(companies, 1):
        sym_raw = company.get("symbol", "")
        sym = str(company.get("screener_symbol") or sym_raw).upper()
        name = company.get("name", sym)
        is_etf = sym in ETF_SYMBOLS or any(e in sym_raw.upper() for e in ETF_SYMBOLS)

        print(f"[{i:3d}/{len(companies)}] {sym:<20} {name[:40]}")

        # ── Step 1: Ensure folder structure ─────────────────────────────────
        folder_result = ensure_folders(sym, data_root, dry_run=dry_run)
        if folder_result["created"]:
            print(f"         Created folders: {', '.join(folder_result['created'])}")
            summary["folders_created"] += len(folder_result["created"])

        if folders_only or dry_run:
            continue

        # ── concalls_only: only fetch transcripts + PPTs ──────────────────────
        if concalls_only:
            if is_etf:
                print(f"         [SKIP] ETF — no concall data")
                summary["skipped_etf"] += 1
                continue
            try:
                r = concall_fetcher.run(company, config, limit=transcript_limit)
                status = r.get("status", "error")
                d = r.get("data", {})
                n_tr = len(d.get("transcripts_downloaded", []))
                n_pp = len(d.get("ppts_downloaded", []))
                icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
                print(f"         {icon} Concalls: +{n_tr} transcripts, +{n_pp} PPTs [{status}]")
                if n_tr > 0 or n_pp > 0:
                    summary["concalls_fetched"] += 1
            except Exception as exc:
                print(f"         ✗ Concalls ERROR: {exc}")
                summary["errors"].append({"symbol": sym, "step": "concalls", "error": str(exc)})
            continue

        if is_etf:
            print(f"         [SKIP] ETF — no Screener concall/insights data")
            summary["skipped_etf"] += 1
            continue

        screener_url = company.get("screener_url", "")
        if not screener_url:
            print(f"         [WARN] No screener_url — skipping Screener steps")
            continue

        # ── Step 2: Insights ─────────────────────────────────────────────────
        if not full and insights_only:
            status = "error"  # default; overwritten by run()
            try:
                r = insights_fetcher.run(company, config, refresh=refresh_insights)
                status = r.get("status", "error")
                n_ins = r.get("data", {}).get("insights_count", 0)
                error_str = r.get("error") or ""
                if status in ("success", "skipped"):
                    icon = "✓"
                    summary["insights_fetched"] += 1
                elif status == "no_data":
                    icon = "–"
                    summary["insights_no_data"] += 1
                else:
                    icon = "✗"
                    summary["insights_errors"] += 1
                print(f"         {icon} Insights: {n_ins} items [{status}]")

                # ── Stop immediately on IP block signals ──────────────────
                if status == "error" and _looks_like_block(error_str):
                    print()
                    print("  ✗  IP BLOCK DETECTED — stopping batch immediately.")
                    print(f"     Signal: {error_str[:120]}")
                    print("     Switch to a different network (mobile hotspot / VPN) and retry.")
                    print()
                    summary["blocked"] = True
                    break

                # ── Abort if Chrome disappeared mid-batch ─────────────────
                if status == "error" and "Chrome CDP not available" in error_str:
                    print()
                    print("  ✗  Chrome CDP lost mid-batch — stopping. Relaunch Chrome and retry.")
                    print()
                    break

            except Exception as exc:
                exc_str = str(exc)
                print(f"         ✗ Insights ERROR: {exc_str}")
                summary["insights_errors"] += 1
                summary["errors"].append({"symbol": sym, "step": "insights", "error": exc_str})
                if _looks_like_block(exc_str):
                    print("  ✗  IP BLOCK DETECTED — stopping batch immediately.")
                    summary["blocked"] = True
                    break

            if status != "skipped":
                _inter_company_delay()

        # ── Step 3: Full data fetch — per-type skip logic ────────────────────
        if full:
            company_dir = data_root / sym
            _blocked = False  # local stop flag; set True on block signal
            _screener_calls = 0  # track CDP calls within this company

            # ── Transcripts + PPTs (httpx PDF download — not a scrape, safe) ──
            # Always attempt fetch — concall_fetcher skips already-downloaded files
            needs_tr  = True
            needs_ppt = True
            if needs_tr or needs_ppt:
                try:
                    r = concall_fetcher.run(company, config, limit=transcript_limit)
                    status = r.get("status", "error")
                    d = r.get("data", {})
                    n_tr = len(d.get("transcripts_downloaded", []))
                    n_pp = len(d.get("ppts_downloaded", []))
                    icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
                    print(f"         {icon} Concalls: +{n_tr} transcripts, +{n_pp} PPTs [{status}]")
                    if n_tr > 0 or n_pp > 0:
                        summary["concalls_fetched"] += 1
                except Exception as exc:
                    print(f"         ✗ Concalls ERROR: {exc}")
                    summary["errors"].append({"symbol": sym, "step": "concalls", "error": str(exc)})
            else:
                n_tr = len(list(company_dir.glob("transcripts/*.pdf")))
                n_pp = len(list(company_dir.glob("ppt/*.pdf")))
                print(f"         – Concalls: {n_tr} transcripts, {n_pp} PPTs [skip]")

            # ── Financials (CDP Screener page) ────────────────────────────────
            if not _blocked and _needs_fetch(company_dir, "financials"):
                if _screener_calls > 0:
                    _inter_step_delay()
                try:
                    r = financial_fetcher.run(company, config)
                    status = r.get("status", "error")
                    error_str = r.get("error") or ""
                    icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
                    print(f"         {icon} Financials [{status}]")
                    if status == "success":
                        summary["financials_fetched"] += 1
                    if status == "error" and _looks_like_block(error_str):
                        print("  ✗  IP BLOCK DETECTED — stopping batch immediately.")
                        summary["blocked"] = True
                        _blocked = True
                except Exception as exc:
                    exc_str = str(exc)
                    print(f"         ✗ Financials ERROR: {exc_str}")
                    summary["errors"].append({"symbol": sym, "step": "financials", "error": exc_str})
                    if _looks_like_block(exc_str):
                        summary["blocked"] = True
                        _blocked = True
                _screener_calls += 1
            elif not _needs_fetch(company_dir, "financials"):
                print(f"         – Financials: already present [skip]")

            # ── Shareholding (CDP Screener page) ──────────────────────────────
            if not _blocked and _needs_fetch(company_dir, "shareholding"):
                if _screener_calls > 0:
                    _inter_step_delay()
                try:
                    r = shareholding_fetcher.run(company, config)
                    status = r.get("status", "error")
                    error_str = r.get("error") or ""
                    icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
                    print(f"         {icon} Shareholding [{status}]")
                    if status == "success":
                        summary["shareholding_fetched"] += 1
                    if status == "error" and _looks_like_block(error_str):
                        print("  ✗  IP BLOCK DETECTED — stopping batch immediately.")
                        summary["blocked"] = True
                        _blocked = True
                except Exception as exc:
                    exc_str = str(exc)
                    print(f"         ✗ Shareholding ERROR: {exc_str}")
                    summary["errors"].append({"symbol": sym, "step": "shareholding", "error": exc_str})
                    if _looks_like_block(exc_str):
                        summary["blocked"] = True
                        _blocked = True
                _screener_calls += 1
            elif not _needs_fetch(company_dir, "shareholding"):
                print(f"         – Shareholding: already present [skip]")

            # ── Insights (CDP Screener page) ──────────────────────────────────
            if not _blocked and _needs_fetch(company_dir, "insights"):
                if _screener_calls > 0:
                    _inter_step_delay()
                try:
                    r = insights_fetcher.run(company, config, refresh=refresh_insights)
                    status = r.get("status", "error")
                    n_ins = r.get("data", {}).get("insights_count", 0)
                    error_str = r.get("error") or ""
                    icon = "✓" if status in ("success", "skipped") else ("–" if status == "no_data" else "✗")
                    print(f"         {icon} Insights: {n_ins} items [{status}]")
                    if status in ("success", "skipped"):
                        summary["insights_fetched"] += 1
                    elif status == "no_data":
                        summary["insights_no_data"] += 1
                    else:
                        summary["insights_errors"] += 1
                    if status == "error" and _looks_like_block(error_str):
                        print("  ✗  IP BLOCK DETECTED — stopping batch immediately.")
                        summary["blocked"] = True
                        _blocked = True
                    if status == "error" and "Chrome CDP not available" in error_str:
                        print("  ✗  Chrome CDP lost mid-batch — stopping.")
                        _blocked = True
                except Exception as exc:
                    exc_str = str(exc)
                    print(f"         ✗ Insights ERROR: {exc_str}")
                    summary["insights_errors"] += 1
                    summary["errors"].append({"symbol": sym, "step": "insights", "error": exc_str})
                    if _looks_like_block(exc_str):
                        summary["blocked"] = True
                        _blocked = True
                _screener_calls += 1
            elif not _needs_fetch(company_dir, "insights"):
                print(f"         – Insights: already present [skip]")

            if _blocked:
                break

            if _screener_calls > 0:
                _inter_company_delay()

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"  SUMMARY")
    print(f"{'=' * 65}")
    print(f"  Companies processed  : {summary['total']}")
    print(f"  New folders created  : {summary['folders_created']}")
    if not folders_only and not dry_run:
        if full:
            print(f"  Concalls fetched     : {summary['concalls_fetched']}")
            print(f"  Financials fetched   : {summary['financials_fetched']}")
            print(f"  Shareholding fetched : {summary['shareholding_fetched']}")
        print(f"  Insights fetched     : {summary['insights_fetched']}")
        print(f"  Insights not found   : {summary['insights_no_data']}")
        print(f"  Insights errors      : {summary['insights_errors']}")
        print(f"  ETFs skipped         : {summary['skipped_etf']}")
        if summary.get('blocked'):
            print(f"  ✗  STOPPED EARLY: IP block detected")
    if summary["errors"]:
        print(f"\n  Errors ({len(summary['errors'])}):")
        for e in summary["errors"][:10]:
            print(f"    {e['symbol']}: [{e['step']}] {e['error'][:80]}")
    print()

    return summary


def _print_insights_report(data_root: Path, companies: list[dict]) -> None:
    """
    Read all saved insights files and print a consolidated report.
    """
    print(f"\n{'=' * 65}")
    print(f"  INSIGHTS REPORT — All Companies")
    print(f"{'=' * 65}\n")

    has_any = False
    for company in companies:
        sym = str(company.get("screener_symbol") or company.get("symbol", "")).upper()
        name = company.get("name", sym)
        insights_dir = data_root / sym / "insights"
        if not insights_dir.exists():
            continue
        files = sorted(insights_dir.glob(f"{sym}_insights_*.txt"), reverse=True)
        if not files:
            continue

        latest = files[0]
        content = latest.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        if not lines:
            continue

        has_any = True
        print(f"## {sym} — {name}")
        for line in lines:
            print(f"  {line}")
        print()

    if not has_any:
        print("  No insights found yet. Run without --report first to fetch insights.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ensure folder structure and data for all portfolio + watchlist companies"
    )
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Process specific symbols only")
    parser.add_argument("--folders-only", action="store_true",
                        help="Only create folder structure, no data fetch")
    parser.add_argument("--insights-only", action="store_true",
                        help="Create folders + fetch insights only (faster)")
    parser.add_argument("--concalls-only", action="store_true",
                        help="Fetch transcripts + PPTs for all companies (no Chrome needed)")
    parser.add_argument("--full", action="store_true",
                        help="Full data fetch: transcripts, PPTs, news, shareholding, financials, insights")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing anything")
    parser.add_argument("--refresh-insights", action="store_true",
                        help="Force re-fetch insights even if already fetched today")
    parser.add_argument("--transcript-limit", type=int, default=999,
                        help="Max transcripts/PPTs to download per company (default 999 = all)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Max companies per run to avoid rate limiting (e.g. --batch-size 30)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip first N companies (use with --batch-size to process in chunks)")
    parser.add_argument("--report", action="store_true",
                        help="Print consolidated insights report from saved files")
    args = parser.parse_args()

    if args.report:
        cfg = get_config()
        data_root = PROJECT_ROOT / cfg["paths"]["data_root"]
        all_companies = _load_all_companies()
        if args.symbols:
            requested = {s.upper() for s in args.symbols}
            all_companies = [
                c for c in all_companies
                if str(c.get("screener_symbol") or c.get("symbol", "")).upper() in requested
            ]
        _print_insights_report(data_root, all_companies)
        sys.exit(0)

    run(
        symbols=args.symbols,
        folders_only=args.folders_only,
        insights_only=args.insights_only,
        concalls_only=args.concalls_only,
        full=args.full,
        dry_run=args.dry_run,
        refresh_insights=args.refresh_insights,
        transcript_limit=args.transcript_limit,
        batch_size=args.batch_size,
        offset=args.offset,
    )
