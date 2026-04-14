"""
Workflow: Watchlist Entry Alerts
==================================
For every stock in watchlist.yaml, fetches current price + 40/99 DMA and
categorises each as a fresh-entry opportunity or a hold-off signal.

Categories (based on price vs 99 DMA):
  🟢 Fresh Entry   — not held, price below 99 DMA
  🔵 Accumulate    — not held, above 99 DMA but below 40 DMA
  🟡 Watch         — not held, price within ±20% of 99 DMA
  ⚠️  Extended      — not held, price >+20% above 99 DMA
  ✅ Already Held  — in current portfolio (with DMA signal for adding more)

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


def _load_holdings(user_dir: Path) -> dict[str, dict]:
    """Return {symbol: holding_dict} for the latest snapshot."""
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return {}
    date_dirs = sorted(
        (d for d in holdings_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    for d in date_dirs:
        hf = d / "holdings.json"
        if hf.exists():
            holdings = json.loads(hf.read_text())
            return {h["symbol"].upper(): h for h in holdings}
    return {}


def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio user directories found.")
    if user_id:
        matches = [d for d in dirs if d.name.upper() == user_id.upper()]
        if matches:
            return matches[0]
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users found: {[d.name for d in dirs]}. Pass --user <ID>.")


# ── report builder ─────────────────────────────────────────────────────────────

def _dma_signal(ltp: float, dma40: float | None, dma99: float | None) -> tuple[str, float | None]:
    """Return (category_key, pct_vs_99) based on price vs DMA."""
    if dma99 is None:
        return "no_dma", None
    pct = (ltp - dma99) / dma99 * 100
    if ltp < dma99:
        return "entry", pct
    if dma40 and ltp < dma40:
        return "accum", pct
    if pct >= 20:
        return "extended", pct
    return "watch", pct


def build_alerts(
    watchlist: list[dict],
    holdings: dict[str, dict],
    dma_results: dict[str, dict],
    snapshot_date: str,
    user_id: str,
) -> str:
    lines: list[str] = [
        f"# Watchlist Entry Alerts",
        f"**{user_id}  ·  {snapshot_date}**\n",
        f"*{len(watchlist)} stocks on watchlist — fresh entry signals based on 40/99 DMA*\n",
    ]

    buckets: dict[str, list[dict]] = {
        "entry": [], "accum": [], "watch": [], "extended": [], "no_dma": [],
        "held_entry": [], "held_accum": [], "held_ok": [],
    }

    for w in watchlist:
        sym   = w["symbol"].upper()
        exch  = w.get("exchange", "NSE")
        name  = w.get("name", sym)
        reason = w.get("watch_reason", "")
        d     = dma_results.get(sym) or {}
        dma40  = d.get("dma40")
        dma99  = d.get("dma99")
        ltp_yf = d.get("last_price")          # from yfinance if not held

        held = holdings.get(sym)
        ltp  = held["last_price"] if held else ltp_yf

        row = {
            "symbol": sym, "name": name, "exchange": exch,
            "reason": reason,
            "ltp": ltp, "dma40": dma40, "dma99": dma99,
            "held": held is not None,
            "alloc_pct": held["pct_of_portfolio"] if held else 0.0,
            "pnl_pct": held["pnl_pct"] if held else None,
            "source": d.get("source", "—"),
        }

        if ltp is None:
            buckets["no_dma"].append({**row, "pct99": None})
            continue

        cat, pct99 = _dma_signal(ltp, dma40, dma99)
        row["pct99"] = pct99

        if held:
            # Already held — classify by SIP signal
            if cat == "entry":
                buckets["held_entry"].append(row)
            elif cat == "accum":
                buckets["held_accum"].append(row)
            else:
                buckets["held_ok"].append(row)
        else:
            buckets[cat].append(row)

    def _row(r: dict, signal: str = "") -> str:
        ltp_s   = f"₹{r['ltp']:.1f}"   if r.get("ltp")   else "—"
        d40_s   = f"₹{r['dma40']:.0f}"  if r.get("dma40")  else "—"
        d99_s   = f"₹{r['dma99']:.0f}" if r.get("dma99") else "—"
        vs99_s  = _pct(r["pct99"])      if r.get("pct99") is not None else "—"
        alloc_s = f"{r['alloc_pct']:.1f}%" if r["held"] else "not held"
        pnl_s   = _pct(r["pnl_pct"])     if r.get("pnl_pct") is not None else "—"
        return (
            f"| **{r['symbol']}** | {r['name'][:28]} | {ltp_s} "
            f"| {d40_s} | {d99_s} | {vs99_s} | {alloc_s} | {pnl_s} | {signal} |"
        )

    hdr = "| Stock | Name | LTP | 40 DMA | 99 DMA | vs 99 | Position | P&L% | Signal |"
    sep = "|---|---|---|---|---|---|---|---|---|"

    if buckets["entry"]:
        lines.append(f"## 🟢 Fresh Entry Zone — below 99 DMA ({len(buckets['entry'])} stocks)")
        lines.append("*Price is below 99 DMA — strong entry / SIP start signal*\n")
        lines += [hdr, sep]
        for r in sorted(buckets["entry"], key=lambda x: x["pct99"] or 0):
            lines.append(_row(r, "🟢 SIP start"))
            if r["reason"]:
                lines.append(f"|  | *{r['reason'][:60]}* | | | | | | | |")
        lines.append("")

    if buckets["accum"]:
        lines.append(f"## 🔵 Accumulate Zone — above 99 DMA, below 40 DMA ({len(buckets['accum'])} stocks)")
        lines.append("*Momentum cooling off above floor — good staggered entry*\n")
        lines += [hdr, sep]
        for r in sorted(buckets["accum"], key=lambda x: x["pct99"] or 0):
            lines.append(_row(r, "🔵 Accum"))
            if r["reason"]:
                lines.append(f"|  | *{r['reason'][:60]}* | | | | | | | |")
        lines.append("")

    if buckets["watch"]:
        lines.append(f"## 🟡 Watch — within normal range ({len(buckets['watch'])} stocks)")
        lines.append("*No urgent signal — monitor for pullbacks*\n")
        lines += [hdr, sep]
        for r in sorted(buckets["watch"], key=lambda x: x["pct99"] or 0):
            lines.append(_row(r, "🟡 watch"))
        lines.append("")

    if buckets["extended"]:
        lines.append(f"## ⚠️  Extended — >+20% above 99 DMA ({len(buckets['extended'])} stocks)")
        lines.append("*Avoid initiating position at current levels — wait for pullback*\n")
        lines += [hdr, sep]
        for r in sorted(buckets["extended"], key=lambda x: -(x["pct99"] or 0)):
            lines.append(_row(r, "⚠️ Extended"))
        lines.append("")

    # ── Already held ───────────────────────────────────────────────────────────────
    held_signals = {id(r): ("🟢 add more" if r in buckets["held_entry"] else ("🔵 small add" if r in buckets["held_accum"] else "🟡 hold")) for r in buckets["held_entry"] + buckets["held_accum"] + buckets["held_ok"]}
    held_all = buckets["held_entry"] + buckets["held_accum"] + buckets["held_ok"]
    if held_all:
        lines.append(f"## ✅ Already Held ({len(held_all)} stocks)")
        lines.append("*Current position + SIP signal for adding more*\n")
        lines += [hdr, sep]
        for r in sorted(held_all, key=lambda x: x.get("pct99") or 0):
            lines.append(_row(r, held_signals[id(r)]))
        lines.append("")

    if buckets["no_dma"]:
        syms = ", ".join(r["symbol"] for r in buckets["no_dma"])
        lines.append(f"*No price/DMA data available for: {syms}*\n")

    lines.append("---")
    lines.append(f"*DMA sources: {', '.join(sorted({r['source'] for r in sum(buckets.values(), []) if r.get('source') and r['source'] != '—'}))}*")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(user_id: str | None = None) -> Path | None:
    from skills.dma_fetcher import get_dma  # noqa: PLC0415

    watchlist = _load_watchlist()
    if not watchlist:
        print("[warn] watchlist.yaml is empty — nothing to alert on.")
        return None

    user_dir = _find_user_dir(user_id)
    holdings = _load_holdings(user_dir)
    snapshot_date = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  WATCHLIST ALERTS  —  {user_dir.name}  ·  {snapshot_date}")
    print(f"{'='*60}")
    print(f"  {len(watchlist)} stocks on watchlist  |  {len(holdings)} holdings loaded\n")

    # Fetch DMA (and last_price for non-held stocks)
    dma_results: dict[str, dict] = {}
    for i, w in enumerate(watchlist, 1):
        sym  = w["symbol"].upper()
        exch = w.get("exchange", "NSE")
        held = holdings.get(sym)
        print(f"  [{i}/{len(watchlist)}] {sym} ... ", end="", flush=True)
        result = get_dma(sym, exch, periods=(40, 99), verbose=False)
        dma_results[sym] = result
        dma99 = result.get("dma99")
        ltp    = held["last_price"] if held else result.get("last_price")
        if ltp and dma99:
            pct = (ltp - dma99) / dma99 * 100
            signal = "🟢 Entry" if ltp < dma99 else ("🔵 Accum" if pct < 5 else ("⚠️ Ext" if pct > 20 else "🟡 Watch"))
            print(f"LTP={ltp:.1f}  99DMA={dma99:.0f}  ({_pct(pct)})  {signal}")
        elif dma99:
            print(f"99DMA={dma99:.0f}  (no LTP)")
        else:
            print("no DMA data")

    # Enrich holdings with pct_of_portfolio if not already present
    if holdings:
        total = sum(h.get("current_value", 0) for h in holdings.values())
        for h in holdings.values():
            if "pct_of_portfolio" not in h and total:
                h["pct_of_portfolio"] = h.get("current_value", 0) / total * 100

    report_md = build_alerts(watchlist, holdings, dma_results, snapshot_date, user_dir.name)

    out_dir = user_dir / "holdings" / snapshot_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "watchlist_alerts.md"
    out_file.write_text(report_md, encoding="utf-8")
    print(f"\n✓ Alerts saved → {out_file}\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Watchlist entry alerts")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    args = parser.parse_args()
    run(user_id=args.user)
