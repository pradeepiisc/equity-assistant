"""
Workflow: Onboard Company
==========================
One-command onboarding for a new watchlist company.

Steps:
  1. Add entry to watchlist.yaml
  2. Create data/companies/{SYMBOL}/ folder structure
  3. Fetch raw data  (data_fetch: concalls, news, shareholding, financials, insights)
  4. Extract insight values  (extract_insights_values: LLM metric table from PDFs)
  5. Fetch ValuePickr forum analysis  (valuepickr_fetcher)
  6. Run full analysis  (full_company_analysis: transcript + financials + valuation → master_report)

Usage:
    python -m workflows.onboard_company EIEL \\
        --name "Enviro Infra Engineers Ltd" \\
        --sector "Water & Wastewater EPC" \\
        --screener-url https://www.screener.in/company/EIEL/ \\
        --bse-code 544290 \\
        --reason "Water recycling theme; Jal Jeevan Mission tailwind"

    # Skip the LLM-heavy steps if you just want raw data first:
    python -m workflows.onboard_company EIEL --name "..." --no-analysis

    # Skip insights-fetcher if Chrome CDP is not running:
    python -m workflows.onboard_company EIEL --name "..." --skip-insights
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"
DATA_DIR = PROJECT_ROOT / "data" / "companies"

COMPANY_SUBDIRS = ["transcripts", "ppt", "news", "shareholding", "financials", "insights", "reports"]


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH) as f:
            return yaml.safe_load(f) or {"stocks": []}
    return {"stocks": []}


def _symbol_in_watchlist(symbol: str, wl: dict) -> bool:
    return any(s.get("symbol", "").upper() == symbol.upper() for s in wl.get("stocks", []))


def _add_to_watchlist(
    symbol: str,
    name: str,
    exchange: str,
    sector: str,
    screener_url: str,
    bse_code: str,
    watch_reason: str,
    peers: list[str],
) -> bool:
    """Append entry to watchlist.yaml. Returns True if added, False if already present."""
    wl = _load_watchlist()
    if _symbol_in_watchlist(symbol, wl):
        print(f"  [watchlist] {symbol} already in watchlist.yaml — skipping add")
        return False

    entry: dict = {
        "symbol": symbol.upper(),
        "name": name,
        "exchange": exchange.upper(),
        "sector": sector,
        "screener_url": screener_url,
        "bse_code": str(bse_code) if bse_code else "",
        "watch_reason": watch_reason,
        "peers": peers,
    }
    wl.setdefault("stocks", []).append(entry)

    with open(WATCHLIST_PATH, "w") as f:
        yaml.dump(wl, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"  [watchlist] ✓ Added {symbol.upper()} to watchlist.yaml")
    return True


def _create_folders(symbol: str) -> Path:
    company_dir = DATA_DIR / symbol.upper()
    for sub in COMPANY_SUBDIRS:
        (company_dir / sub).mkdir(parents=True, exist_ok=True)
    print(f"  [folders]   ✓ {company_dir}")
    return company_dir


def _step(label: str, fn, *args, **kwargs):
    """Run a step, catch exceptions, print result."""
    print(f"\n{'─'*60}")
    print(f"  STEP: {label}")
    print(f"{'─'*60}")
    try:
        return fn(*args, **kwargs)
    except SystemExit as e:
        print(f"  [!] Step exited with code {e.code} — continuing pipeline")
        return None
    except Exception as exc:
        print(f"  [!] Step failed: {exc}")
        return None


# ── pipeline steps ────────────────────────────────────────────────────────────

def _run_data_fetch(symbol: str, skip_cdp: bool) -> None:
    from workflows.data_fetch import run as data_fetch_run  # noqa: PLC0415
    data_fetch_run(
        symbol=symbol,
        fetch_transcripts=True,
        fetch_news=True,
        fetch_shareholding=not skip_cdp,
        fetch_financials=not skip_cdp,
        fetch_insights=not skip_cdp,
        transcript_limit=8,   # fetch more for a new company
        news_limit=10,
    )


def _run_extract_insights(symbol: str) -> None:
    from workflows.extract_insights_values import run as eiv_run  # noqa: PLC0415
    eiv_run(symbols=[symbol])


def _run_valuepickr(symbol: str, name: str) -> None:
    from skills.valuepickr_fetcher import run as vp_run  # noqa: PLC0415
    path = vp_run(symbol=symbol, company_name=name, verbose=True)
    if not path:
        print(f"  [valuepickr] No thread found for {symbol} — continuing")


def _run_full_analysis(symbol: str) -> None:
    from workflows.full_company_analysis import run as fca_run  # noqa: PLC0415
    fca_run(symbol)


# ── main run ──────────────────────────────────────────────────────────────────

def _check_cdp() -> bool:
    """Returns True if Chrome CDP is reachable."""
    try:
        from skills.cdp_helper import is_available  # noqa: PLC0415
        return is_available()
    except Exception:
        return False


def run(
    symbol: str,
    name: str,
    exchange: str = "NSE",
    sector: str = "",
    screener_url: str = "",
    bse_code: str = "",
    watch_reason: str = "",
    peers: list[str] | None = None,
    skip_cdp: bool = False,
    skip_analysis: bool = False,
) -> None:
    symbol = symbol.upper()
    peers = peers or []

    print(f"\n{'='*60}")
    print(f"  ONBOARD COMPANY: {symbol}  —  {name}")
    print(f"{'='*60}\n")

    # Auto-detect Chrome CDP availability
    if not skip_cdp:
        cdp_ok = _check_cdp()
        if not cdp_ok:
            skip_cdp = True
            print("  ⚠️  Chrome CDP not detected.")
            print("  Shareholding, financials, and Screener insights will be SKIPPED.")
            print("  Transcripts + news will still be fetched (no CDP needed).")
            print("  To fetch CDP data after onboarding:")
            print("    1. bash scripts/launch_chrome_debug.sh  (Terminal.app, NOT Windsurf)")
            print(f"    2. python -m workflows.data_fetch {symbol}")
            print()
        else:
            print("  ✓ Chrome CDP detected — all steps will run (incl. shareholding, financials, insights)\n")

    # 1. watchlist.yaml
    print("STEP 1/6 — watchlist.yaml")
    _add_to_watchlist(
        symbol=symbol,
        name=name,
        exchange=exchange,
        sector=sector,
        screener_url=screener_url,
        bse_code=bse_code,
        watch_reason=watch_reason,
        peers=peers,
    )

    # 2. folder structure
    print("\nSTEP 2/6 — folder structure")
    _create_folders(symbol)

    # 3. data fetch
    cdp_note = " [shareholding/financials/insights SKIPPED — Chrome CDP not running]" if skip_cdp else ""
    _step(
        f"3/6 — data_fetch ({symbol}): concalls, news" + ("" if skip_cdp else ", shareholding, financials, insights") + cdp_note,
        _run_data_fetch, symbol, skip_cdp,
    )

    # 4. extract insight values (LLM over transcripts)
    _step("4/6 — extract_insights_values (LLM quarterly metric table)", _run_extract_insights, symbol)

    # 5. valuepickr forum
    _step("5/6 — valuepickr_fetcher (expert forum analysis)", _run_valuepickr, symbol, name)

    # 6. full LLM analysis
    if skip_analysis:
        print(f"\n{'─'*60}")
        print("  STEP 6/6 — full_company_analysis [SKIPPED via --no-analysis]")
        print("  Run manually when ready:")
        print(f"    python -m workflows.full_company_analysis {symbol}")
    else:
        _step("6/6 — full_company_analysis (transcript + financials + valuation → master_report)", _run_full_analysis, symbol)

    print(f"\n{'='*60}")
    print(f"  ONBOARD COMPLETE: {symbol}")
    print(f"{'='*60}")
    print(f"  Reports → data/companies/{symbol}/reports/")
    print(f"  ValuePickr → data/companies/{symbol}/valuepickr.md")
    print(f"  Master report → data/companies/{symbol}/reports/master_report.md")
    if skip_cdp:
        print()
        print("  ⚠️  CDP steps were skipped. To complete the fetch:")
        print("    1. bash scripts/launch_chrome_debug.sh  (Terminal.app, NOT Windsurf)")
        print(f"    2. python -m workflows.data_fetch {symbol}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Onboard a new watchlist company: add YAML + fetch all data + full analysis"
    )
    parser.add_argument("symbol", help="Trading symbol e.g. EIEL")
    parser.add_argument("--name", required=True, help="Company full name")
    parser.add_argument("--exchange", default="NSE", help="Exchange (default: NSE)")
    parser.add_argument("--sector", default="", help="Sector description")
    parser.add_argument("--screener-url", default="", dest="screener_url",
                        help="Screener.in URL e.g. https://www.screener.in/company/EIEL/")
    parser.add_argument("--bse-code", default="", dest="bse_code", help="BSE numeric code")
    parser.add_argument("--reason", default="", dest="watch_reason",
                        help="Watch reason / investment thesis note")
    parser.add_argument("--peers", nargs="*", default=[],
                        help="Peer symbols e.g. --peers WABAG ENVIROTECH")
    parser.add_argument("--skip-cdp", action="store_true",
                        help="Force-skip CDP steps (shareholding, financials, insights). By default auto-detected.")
    parser.add_argument("--no-analysis", action="store_true",
                        help="Skip LLM analysis steps (data fetch only)")
    args = parser.parse_args()

    run(
        symbol=args.symbol,
        name=args.name,
        exchange=args.exchange,
        sector=args.sector,
        screener_url=args.screener_url,
        bse_code=args.bse_code,
        watch_reason=args.watch_reason,
        peers=args.peers,
        skip_cdp=args.skip_cdp,
        skip_analysis=args.no_analysis,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
