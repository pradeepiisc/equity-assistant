"""
Workflow: Watchlist Entry Alerts
==================================
For every stock in watchlist.yaml, fetches current price + 40/99 DMA and
displays the DMA positioning for reference.

Output:
  • Printed table to console
  • Saved to portfolio/{user}/{today}/watchlist_alerts.md

Usage:
    python -m workflows.watchlist_alerts
    python -m workflows.watchlist_alerts --user ZV3899
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"


# ── helpers ───────────────────────────────────────────────────────────────────

def _inr(v: float) -> str:
    return f"₹{abs(v):,.0f}"


def _pct(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.1f}%"


def _load_watchlist() -> list[dict]:
    path = PROJECT_ROOT / "watchlist.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


def _load_all_holdings() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Return ({symbol: [held_dict_per_user]}, {symbol: dict_with_pct_and_pnl_for_display})."""
    holdings_by_user: dict[str, list[dict]] = {}
    
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    for user_dir in dirs:
        holdings_dir = user_dir / "holdings"
        hf = holdings_dir / "holdings.json"
        if not hf.exists():
            continue
        try:
            holdings = json.loads(hf.read_text())
            total = sum(h.get("current_value", 0) for h in holdings)
            
            for h in holdings:
                sym = h.get("tradingsymbol", h.get("symbol", "")).upper()
                if not sym or h.get("quantity", 0) <= 0:
                    continue
                    
                if "pct_of_portfolio" not in h and total:
                    h["pct_of_portfolio"] = h.get("current_value", 0) / total * 100
                h["_user"] = user_dir.name
                
                if sym not in holdings_by_user:
                    holdings_by_user[sym] = []
                holdings_by_user[sym].append(h)
        except Exception:
            pass

    display_holdings = {}
    for sym, h_list in holdings_by_user.items():
        # Combine display info (take first for ltp, sum alloc, average pnl)
        held_users = [h["_user"] for h in h_list]
        alloc_pct = sum(h.get("pct_of_portfolio", 0.0) for h in h_list) / len(h_list)
        pnl_pcts = [h.get("pnl_pct") for h in h_list if h.get("pnl_pct") is not None]
        avg_pnl = sum(pnl_pcts)/len(pnl_pcts) if pnl_pcts else None
        
        display_holdings[sym] = {
            "last_price": h_list[0].get("last_price"),
            "users": held_users,
            "pct_of_portfolio": alloc_pct,
            "pnl_pct": avg_pnl
        }
        
    return holdings_by_user, display_holdings


# ── report builder ─────────────────────────────────────────────────────────────

def build_alerts(
    watchlist: list[dict],
    display_holdings: dict[str, dict],
    dma_results: dict[str, dict],
    snapshot_date: str,
) -> str:
    lines: list[str] = [
        f"# Watchlist DMA Overview",
        f"**Global  ·  {snapshot_date}**\n",
        f"*{len(watchlist)} stocks on watchlist — DMA positioning for reference*\n",
    ]

    rows_with_data: list[dict] = []
    rows_held: list[dict] = []
    rows_no_dma: list[str] = []
    sources: set[str] = set()

    for w in watchlist:
        sym   = w["symbol"].upper()
        exch  = w.get("exchange", "NSE")
        name  = w.get("name", sym)
        reason = w.get("watch_reason", "")
        d     = dma_results.get(sym) or {}
        dma40  = d.get("dma40")
        dma99  = d.get("dma99")
        ltp_yf = d.get("last_price")

        held = display_holdings.get(sym)
        ltp  = held["last_price"] if held else ltp_yf

        src = d.get("source", "—")
        if src and src != "—":
            sources.add(src)

        row = {
            "symbol": sym, "name": name, "exchange": exch,
            "reason": reason,
            "ltp": ltp, "dma40": dma40, "dma99": dma99,
            "held": held is not None,
            "users": held["users"] if held else [],
            "alloc_pct": held["pct_of_portfolio"] if held else 0.0,
            "pnl_pct": held["pnl_pct"] if held else None,
        }

        if ltp is None or dma99 is None:
            rows_no_dma.append(sym)
            continue

        row["pct99"] = (ltp - dma99) / dma99 * 100

        if held:
            rows_held.append(row)
        else:
            rows_with_data.append(row)

    def _row(r: dict) -> str:
        ltp_s   = f"₹{r['ltp']:.1f}"   if r.get("ltp")   else "—"
        d40_s   = f"₹{r['dma40']:.0f}"  if r.get("dma40")  else "—"
        d99_s   = f"₹{r['dma99']:.0f}" if r.get("dma99") else "—"
        vs99_s  = _pct(r["pct99"])      if r.get("pct99") is not None else "—"
        if r["held"]:
            alloc_s = f"held ({','.join(r['users'])})"
        else:
            alloc_s = "not held"
        pnl_s   = _pct(r["pnl_pct"])     if r.get("pnl_pct") is not None else "—"
        return (
            f"| **{r['symbol']}** | {r['name'][:28]} | {ltp_s} "
            f"| {d40_s} | {d99_s} | {vs99_s} | {alloc_s} | {pnl_s} |"
        )

    hdr = "| Stock | Name | LTP | 40 DMA | 99 DMA | vs 99 | Position | P&L% |"
    sep = "|---|---|---|---|---|---|---|---|"

    # Not held — sorted by vs 99 DMA ascending
    if rows_with_data:
        lines.append(f"## Watchlist Stocks — Not Held ({len(rows_with_data)} stocks)")
        lines.append("*Sorted by % vs 99 DMA*\n")
        lines += [hdr, sep]
        for r in sorted(rows_with_data, key=lambda x: x.get("pct99") or 0):
            lines.append(_row(r))
            if r["reason"]:
                lines.append(f"|  | *{r['reason'][:60]}* | | | | | | |")
        lines.append("")

    # Already held
    if rows_held:
        lines.append(f"## Already Held ({len(rows_held)} stocks)")
        lines.append("*Current position with DMA data*\n")
        lines += [hdr, sep]
        for r in sorted(rows_held, key=lambda x: x.get("pct99") or 0):
            lines.append(_row(r))
        lines.append("")

    if rows_no_dma:
        lines.append(f"*No price/DMA data available for: {', '.join(rows_no_dma)}*\n")

    lines.append("---")
    lines.append(f"*DMA sources: {', '.join(sorted(sources))}*")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(user_id: str | None = None) -> Path | None:
    from skills.dma_fetcher import get_dma  # noqa: PLC0415

    watchlist = _load_watchlist()
    if not watchlist:
        print("[warn] watchlist.yaml is empty — nothing to alert on.")
        return None

    snapshot_date = date.today().isoformat()
    out_file = PORTFOLIO_DIR / "watchlist_alerts.md"
    
    # "watchlist alerts part is done only once" logic:
    if out_file.exists():
        import os
        from datetime import datetime
        mtime = datetime.fromtimestamp(os.path.getmtime(out_file)).date()
        if mtime == date.today():
            print(f"\n✓ Watchlist alerts already generated today → {out_file}\n")
            return out_file

    holdings_by_user, display_holdings = _load_all_holdings()

    print(f"\n{'='*60}")
    print(f"  WATCHLIST ALERTS  —  Global  ·  {snapshot_date}")
    print(f"{'='*60}")
    print(f"  {len(watchlist)} stocks on watchlist  |  {len(holdings_by_user)} unique holdings across users\n")

    # Fetch DMA (from cache first, then fallback to API)
    dma_results: dict[str, dict] = {}
    cache_file = PORTFOLIO_DIR / ".cache" / f"dma_{snapshot_date}.json"
    cache: dict = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except Exception:
            pass

    for i, w in enumerate(watchlist, 1):
        sym  = w["symbol"].upper()
        exch = w.get("exchange", "NSE")
        held = display_holdings.get(sym)
        print(f"  [{i}/{len(watchlist)}] {sym} ... ", end="", flush=True)
        
        # Check cache
        if sym in cache and (cache[sym].get("dma99") or cache[sym].get("dma40")):
            result = cache[sym]
            print("(cached) ", end="")
        else:
            result = get_dma(sym, exch, periods=(40, 99), verbose=False)
            cache[sym] = result
            
        dma_results[sym] = result
        dma99 = result.get("dma99")
        ltp    = held["last_price"] if held else result.get("last_price")
        
        if ltp and dma99:
            pct = (ltp - dma99) / dma99 * 100
            print(f"LTP={ltp:.1f}  99DMA={dma99:.0f}  ({_pct(pct)})")
        elif dma99:
            print(f"99DMA={dma99:.0f}  (no LTP)")
        else:
            print("no DMA data")

    # Save updated DMA cache
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    report_md = build_alerts(watchlist, display_holdings, dma_results, snapshot_date)

    out_file.write_text(report_md, encoding="utf-8")
    print(f"\n✓ Alerts saved → {out_file}\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Watchlist entry alerts")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    args = parser.parse_args()
    run(user_id=args.user)
