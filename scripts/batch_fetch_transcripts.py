#!/usr/bin/env python3
"""Batch fetch transcripts + PPTs for all companies and prune to latest 4 each.

Usage:
    python scripts/batch_fetch_transcripts.py                     # fetch + prune all
    python scripts/batch_fetch_transcripts.py --prune-only        # just prune, no fetch
    python scripts/batch_fetch_transcripts.py --symbols SYM1 SYM2 # specific symbols
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.concall_fetcher import (
    run as fetch_concalls,
    _prune_old_files,
    _get_transcripts_dir,
    _get_ppt_dir,
    MAX_KEEP_TRANSCRIPTS,
    MAX_KEEP_PPTS,
)
from llm.client import get_config

COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
WATCHLIST_FILE = PROJECT_ROOT / "watchlist.yaml"


def main():
    import yaml

    prune_only = "--prune-only" in sys.argv
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
    companies = []
    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        sym = d.name.upper()
        if only_symbols and sym not in only_symbols:
            continue

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

    mode = "PRUNE ONLY" if prune_only else "FETCH + PRUNE"
    print(f"\n{'=' * 60}")
    print(f"  BATCH TRANSCRIPT {mode}")
    print(f"{'=' * 60}")
    print(f"  Companies: {len(companies)}")
    print(f"  Keep: {MAX_KEEP_TRANSCRIPTS} transcripts, {MAX_KEEP_PPTS} PPTs per company")
    if only_symbols:
        print(f"  Filter: {', '.join(only_symbols)}")
    print()

    if not companies:
        print("  No companies found!")
        return

    cfg = get_config()
    total_downloaded = 0
    total_pruned = 0
    total_failed = 0

    for i, c in enumerate(companies, 1):
        sym = c["symbol"]
        print(f"  [{i:3d}/{len(companies)}] {sym:<16}", end="", flush=True)

        if prune_only:
            t_dir = _get_transcripts_dir(sym)
            p_dir = _get_ppt_dir(sym)
            pt = _prune_old_files(t_dir, MAX_KEEP_TRANSCRIPTS, "transcript", sym)
            pp = _prune_old_files(p_dir, MAX_KEEP_PPTS, "PPT", sym)
            n_pruned = len(pt) + len(pp)
            total_pruned += n_pruned
            t_count = len(list(t_dir.glob("*.pdf")))
            p_count = len(list(p_dir.glob("*.pdf")))
            if n_pruned:
                print(f"  pruned {n_pruned} | kept {t_count}t+{p_count}p")
            else:
                print(f"  ok ({t_count}t+{p_count}p)")
        else:
            try:
                result = fetch_concalls(c, cfg, limit=MAX_KEEP_TRANSCRIPTS)
                dl = len(result["data"].get("transcripts_downloaded", []))
                dl += len(result["data"].get("ppts_downloaded", []))
                pr = len(result["data"].get("pruned_transcripts", []))
                pr += len(result["data"].get("pruned_ppts", []))
                fl = len(result["data"].get("failed", []))
                total_downloaded += dl
                total_pruned += pr
                total_failed += fl
                status = "✓" if result["status"] == "success" else "○"
                print(f"  {status} dl={dl} pruned={pr}")
            except Exception as e:
                total_failed += 1
                print(f"  ✗ {str(e)[:50]}")

            time.sleep(0.5)

    print(f"\n{'=' * 60}")
    print(f"  COMPLETE")
    if prune_only:
        print(f"  Pruned: {total_pruned} files")
    else:
        print(f"  Downloaded: {total_downloaded} | Pruned: {total_pruned} | Failed: {total_failed}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
