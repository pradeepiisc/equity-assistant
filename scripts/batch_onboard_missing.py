"""
Batch Onboard Missing Companies
================================
Adds missing companies from holdings to watchlist.yaml, creates folder structures,
and stores meta.json. Data fetch (CDP-based) is done separately in batches.

Usage:
    python scripts/batch_onboard_missing.py                  # Dry run
    python scripts/batch_onboard_missing.py --execute        # Actually do it
    python scripts/batch_onboard_missing.py --execute --fetch # Also run data_fetch per company
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "companies"
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"
COMPANY_SUBDIRS = ["transcripts", "ppt", "news", "shareholding", "financials", "insights", "reports"]
PLATFORM_SUFFIXES = ("-SM", "-ST", "-BE")


# ── Company name mapping (known Indian stocks) ──────────────────────────────
COMPANY_INFO = {
    "ADVENTHTL":   {"name": "Advani Hotels & Resorts (India) Ltd", "sector": "Hotels & Tourism"},
    "ARMANFIN":    {"name": "Arman Financial Services Ltd", "sector": "NBFC - Microfinance"},
    "AWHCL":       {"name": "Antony Waste Handling Cell Ltd", "sector": "Waste Management"},
    "BELLACASA":   {"name": "Bella Casa Fashion & Retail Ltd", "sector": "Textiles - Home Furnishing"},
    "BHEL":        {"name": "Bharat Heavy Electricals Ltd", "sector": "Capital Goods - Power Equipment"},
    "CHENNPETRO":  {"name": "Chennai Petroleum Corporation Ltd", "sector": "Oil Refining"},
    "CIPLA":       {"name": "Cipla Ltd", "sector": "Pharmaceuticals"},
    "DCXINDIA":    {"name": "DCX Systems Ltd", "sector": "Aerospace & Defence - Electronics"},
    "FEDFINA":     {"name": "Fedbank Financial Services Ltd", "sector": "NBFC"},
    "FREDUN":      {"name": "Fredun Pharmaceuticals Ltd", "sector": "Pharmaceuticals - API"},
    "GATEWAY":     {"name": "Gateway Distriparks Ltd", "sector": "Logistics - Container Freight"},
    "GHCLTEXTIL":  {"name": "GHCL Textiles Ltd", "sector": "Textiles - Home Textiles"},
    "HARIOMPIPE":  {"name": "Hariom Pipe Industries Ltd", "sector": "Steel Pipes"},
    "HEALTHADD":   {"name": "Healthium Medtech Ltd", "sector": "Medical Devices"},
    "HEALTHCARE":  {"name": "Healthcare Global Enterprises Ltd", "sector": "Hospitals & Healthcare"},
    "IDEA":        {"name": "Vodafone Idea Ltd", "sector": "Telecom"},
    "IRMENERGY":   {"name": "IRM Energy Ltd", "sector": "City Gas Distribution"},
    "JSFB":        {"name": "Jana Small Finance Bank Ltd", "sector": "Small Finance Bank"},
    "LOKESHMACH":  {"name": "Lokesh Machines Ltd", "sector": "Auto Ancillary - CNC Machining"},
    "LUXIND":      {"name": "Lux Industries Ltd", "sector": "Textiles - Innerwear"},
    "MAHALIFE":    {"name": "Mahanagar Life Sciences Ltd", "sector": "Pharmaceuticals"},
    "MEDIASSIST":  {"name": "Medi Assist Healthcare Services Ltd", "sector": "Health Insurance TPA"},
    "MPSLTD":      {"name": "MPS Ltd", "sector": "Publishing & e-Learning Platform"},
    "NAZARA":      {"name": "Nazara Technologies Ltd", "sector": "Gaming & Esports"},
    "NEOCHEM":     {"name": "Neogen Chemicals Ltd", "sector": "Specialty Chemicals"},
    "PARADEEP":    {"name": "Paradeep Phosphates Ltd", "sector": "Fertilizers"},
    "PATELENG":    {"name": "Patel Engineering Ltd", "sector": "Infrastructure - EPC"},
    "PPL":         {"name": "Prakash Pipes Ltd", "sector": "PVC Pipes & Fittings"},
    "PRAKASH":     {"name": "Prakash Industries Ltd", "sector": "Steel & Power"},
    "PROTEAN":     {"name": "Protean eGov Technologies Ltd", "sector": "IT - eGovernance"},
    "RPTECH":      {"name": "Rashi Peripherals Ltd", "sector": "IT Distribution"},
    "SAAKSHI":     {"name": "Saakshi Medtech & Panels Ltd", "sector": "Medical Equipment & Panels"},
    "SHRIRAMPPS":  {"name": "Shriram Pistons & Rings Ltd", "sector": "Auto Ancillary - Engine Parts"},
    "SKMEGGPROD":  {"name": "SKM Egg Products Export (India) Ltd", "sector": "Food Processing - Eggs"},
    "STAR":        {"name": "Star Cement Ltd", "sector": "Cement"},
    "STARHEALTH":  {"name": "Star Health & Allied Insurance Co Ltd", "sector": "Health Insurance"},
    "SWSOLAR":     {"name": "Sterling & Wilson Renewable Energy Ltd", "sector": "Renewable Energy EPC"},
    "WEBELSOLAR":  {"name": "Websol Energy System Ltd", "sector": "Solar Manufacturing"},
    "YAAP":        {"name": "Yaarii Digital Integrated Services Ltd", "sector": "Digital Payments"},
}


def _load_holdings() -> dict[str, dict]:
    """Load all holdings from both users, keyed by symbol."""
    all_h: dict[str, dict] = {}
    for user_dir in PORTFOLIO_DIR.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        hf = user_dir / "holdings" / "holdings.json"
        if not hf.exists():
            continue
        holdings = json.loads(hf.read_text())
        for h in holdings:
            sym = h.get("symbol", "")
            if sym and sym not in all_h:
                all_h[sym] = h
    return all_h


def _find_missing() -> list[str]:
    """Find held symbols that don't have a data/companies/ folder."""
    existing = set(os.listdir(DATA_DIR)) if DATA_DIR.exists() else set()
    all_h = _load_holdings()
    missing = []
    for sym in sorted(all_h.keys()):
        if sym in existing:
            continue
        base = sym
        for sfx in PLATFORM_SUFFIXES:
            if sym.endswith(sfx):
                base = sym[: -len(sfx)]
                break
        candidates = [base] + [base + s for s in PLATFORM_SUFFIXES if base + s != sym]
        if not any(c in existing for c in candidates):
            missing.append(sym)
    return missing


def _load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH) as f:
            return yaml.safe_load(f) or {"stocks": []}
    return {"stocks": []}


def _symbol_in_watchlist(symbol: str, wl: dict) -> bool:
    return any(s.get("symbol", "").upper() == symbol.upper() for s in wl.get("stocks", []))


def onboard_company(symbol: str, info: dict, wl: dict, dry_run: bool = True) -> None:
    """Lightweight onboard: watchlist + folders + meta.json."""
    name = info.get("name", symbol)
    sector = info.get("sector", "")
    screener_url = f"https://www.screener.in/company/{symbol}/"
    exchange = info.get("exchange", "NSE")

    action = "[DRY]" if dry_run else "[ADD]"

    # 1. watchlist.yaml
    if _symbol_in_watchlist(symbol, wl):
        print(f"  {action} {symbol:16s} — already in watchlist.yaml")
    else:
        print(f"  {action} {symbol:16s} — {name} ({sector})")
        if not dry_run:
            entry = {
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "sector": sector,
                "screener_url": screener_url,
                "bse_code": "",
                "watch_reason": "Portfolio holding — auto-onboarded",
                "peers": [],
            }
            wl.setdefault("stocks", []).append(entry)

    if dry_run:
        return

    # 2. folder structure
    company_dir = DATA_DIR / symbol
    for sub in COMPANY_SUBDIRS:
        (company_dir / sub).mkdir(parents=True, exist_ok=True)

    # 3. meta.json
    meta_path = company_dir / "meta.json"
    existing_meta = {}
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text())
        except Exception:
            pass
    existing_meta["symbol"] = symbol
    if name and len(name) > 2:
        existing_meta["name"] = name
    if screener_url:
        existing_meta["screener_url"] = screener_url
    if sector:
        existing_meta["sector"] = sector
    meta_path.write_text(json.dumps(existing_meta, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually onboard (default: dry run)")
    parser.add_argument("--fetch", action="store_true", help="Also run data_fetch per company (requires Chrome CDP)")
    parser.add_argument("--delay", type=int, default=8, help="Seconds between data_fetch calls (IP throttle)")
    args = parser.parse_args()

    missing = _find_missing()
    print(f"\n{'='*60}")
    print(f"  BATCH ONBOARD — {len(missing)} missing companies")
    print(f"{'='*60}")
    print(f"  Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    if not args.execute:
        print("  Add --execute to actually onboard\n")

    wl = _load_watchlist()
    holdings = _load_holdings()

    for sym in missing:
        info = COMPANY_INFO.get(sym, {"name": sym, "sector": ""})
        h = holdings.get(sym, {})
        info["exchange"] = h.get("exchange", "NSE")
        onboard_company(sym, info, wl, dry_run=not args.execute)

    if args.execute:
        # Save watchlist
        with open(WATCHLIST_PATH, "w") as f:
            yaml.dump(wl, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        print(f"\n  ✓ Saved watchlist.yaml ({len(wl.get('stocks', []))} total entries)")

        # Optionally run data_fetch
        if args.fetch:
            print(f"\n{'='*60}")
            print(f"  DATA FETCH — {len(missing)} companies (delay={args.delay}s)")
            print(f"{'='*60}\n")
            from workflows.data_fetch import run as data_fetch_run
            for i, sym in enumerate(missing, 1):
                print(f"\n[{i}/{len(missing)}] Fetching data for {sym}...")
                try:
                    data_fetch_run(
                        symbol=sym,
                        fetch_transcripts=True,
                        fetch_news=True,
                        fetch_shareholding=True,
                        fetch_financials=True,
                        fetch_insights=True,
                        fetch_valuepickr=True,
                        interactive=False,
                    )
                except Exception as e:
                    print(f"  [!] {sym} failed: {e}")
                if i < len(missing):
                    print(f"  (waiting {args.delay}s to avoid IP block...)")
                    time.sleep(args.delay)

    print(f"\n{'='*60}")
    print(f"  DONE — {len(missing)} companies processed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
