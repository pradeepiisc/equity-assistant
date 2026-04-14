"""
Workflow: Fix Company Names
============================
Scrapes the correct company name AND sector from each company's Screener.in
page via Chrome CDP, saves them to data/companies/{SYMBOL}/meta.json,
and updates portfolio_companies.yaml and watchlist.yaml in-place.

Usage:
    python -m workflows.fix_company_names [--symbols S1 S2 ...]  [--dry-run]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

from llm.client import get_config
from skills.cdp_helper import is_available as _chrome_available
from skills.company_meta import scrape_company_info, store_company_meta, get_company_name

PROJECT_ROOT = Path(__file__).parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def run(symbols: list[str] | None = None, dry_run: bool = False) -> None:
    if not _chrome_available():
        print("[fix_company_names] ERROR: Chrome CDP not available.")
        print("  Start Chrome: bash scripts/launch_chrome_debug.sh  (from Terminal.app)")
        return

    yaml_files = ["watchlist.yaml", "portfolio_companies.yaml"]
    total_fixed = 0

    for yaml_name in yaml_files:
        yaml_path = PROJECT_ROOT / yaml_name
        if not yaml_path.exists():
            continue

        data = _load_yaml(yaml_path)
        companies = data.get("stocks", [])
        changed = False

        print(f"\n── {yaml_name} ({len(companies)} companies) ──")

        for company in companies:
            sym = company.get("symbol", "").upper()
            if symbols and sym not in symbols:
                continue

            screener_url = company.get("screener_url", "")
            if not screener_url:
                print(f"  [{sym}] SKIP — no screener_url")
                continue

            old_name = company.get("name", sym)
            old_sector = company.get("sector", "")

            # Try scraping name + sector from Screener
            info = scrape_company_info(screener_url)
            scraped_name = info.get("name")
            scraped_sector = info.get("sector")

            if scraped_name and scraped_name != old_name:
                print(f"  [{sym}] NAME  FIXED: {old_name!r}\n                    → {scraped_name!r}")
                if not dry_run:
                    company["name"] = scraped_name
                changed = True
                total_fixed += 1
            elif scraped_name:
                print(f"  [{sym}] NAME  OK: {old_name!r}")
            else:
                print(f"  [{sym}] NAME  WARN: could not scrape, keeping: {old_name!r}")
                scraped_name = old_name

            if scraped_sector and scraped_sector != old_sector:
                print(f"  [{sym}] SECTOR FIXED: {old_sector!r}\n                    → {scraped_sector!r}")
                if not dry_run:
                    company["sector"] = scraped_sector
                changed = True
            elif scraped_sector:
                print(f"  [{sym}] SECTOR OK: {old_sector!r}")

            if not dry_run:
                store_company_meta(
                    sym,
                    name=scraped_name or old_name,
                    screener_url=screener_url,
                    sector=scraped_sector or old_sector,
                )

            time.sleep(0.3)

        if changed and not dry_run:
            _save_yaml(yaml_path, data)
            print(f"  ✅ Saved {yaml_path.name}")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done — {total_fixed} name(s) corrected.")


if __name__ == "__main__":
    symbols_filter: list[str] | None = None
    dry = "--dry-run" in sys.argv

    if "--symbols" in sys.argv:
        idx = sys.argv.index("--symbols")
        symbols_filter = [s.upper() for s in sys.argv[idx + 1:] if not s.startswith("--")]

    run(symbols=symbols_filter, dry_run=dry)
