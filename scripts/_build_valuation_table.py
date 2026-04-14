"""
Build a comprehensive valuation summary table for ALL companies.
Reads master_report.json and valuation_agent.json to extract:
  - Bear / Base / Bull prices
  - Signal / Rating
  - CMP (from portfolio holdings)
  - Upside %
Outputs a markdown table sorted by signal strength.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "companies"
HOLDINGS_DIR = ROOT / "portfolio" / "ZV3899" / "holdings"


def _latest_holdings() -> dict[str, float]:
    """Load last_price from the most recent holdings.json."""
    if not HOLDINGS_DIR.exists():
        return {}
    dates = sorted(d.name for d in HOLDINGS_DIR.iterdir() if d.is_dir())
    if not dates:
        return {}
    for dt in reversed(dates):
        hf = HOLDINGS_DIR / dt / "holdings.json"
        if hf.exists():
            holdings = json.loads(hf.read_text())
            return {h["symbol"]: h.get("last_price", 0) for h in holdings}
    return {}


def _extract_valuation(symbol: str) -> dict | None:
    """Extract valuation data from master_report.json or valuation_agent.json."""
    base = DATA / symbol / "reports"

    for fname in ["master_report.json", "valuation_agent.json"]:
        fp = base / fname
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text())
        except Exception:
            continue

        # Try various key patterns
        bear = data.get("fair_value_low") or data.get("bear_case_price") or data.get("bear_price")
        bull = data.get("fair_value_high") or data.get("bull_case_price") or data.get("bull_price")
        base_price = data.get("base_case_price") or data.get("base_price") or data.get("fair_value_base")
        signal = data.get("entry_signal") or data.get("signal") or data.get("rating") or ""
        upside = data.get("upside_pct") or data.get("upside")
        rating = data.get("rating") or data.get("overall_rating") or ""

        if bear is not None or bull is not None:
            return {
                "bear": bear,
                "base": base_price,
                "bull": bull,
                "signal": signal,
                "rating": rating,
                "upside": upside,
                "source": fname,
            }

    return None


def _get_company_name(symbol: str) -> str:
    meta = DATA / symbol / "meta.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text())
            return d.get("name", symbol)
        except Exception:
            pass
    return symbol


def main():
    prices = _latest_holdings()
    all_symbols = sorted(d.name for d in DATA.iterdir() if d.is_dir())

    rows = []
    no_report = []

    for sym in all_symbols:
        val = _extract_valuation(sym)
        cmp = prices.get(sym)
        name = _get_company_name(sym)

        if val is None:
            no_report.append(sym)
            continue

        bear = val["bear"]
        base = val["base"]
        bull = val["bull"]
        signal = val["signal"]
        rating = val["rating"]
        upside = val["upside"]

        # Calculate upside from base if not provided
        if upside is None and base is not None and cmp:
            try:
                upside = round((float(base) / float(cmp) - 1) * 100, 1)
            except Exception:
                upside = None

        rows.append({
            "symbol": sym,
            "name": name[:30],
            "cmp": cmp,
            "bear": bear,
            "base": base,
            "bull": bull,
            "signal": signal or rating,
            "upside": upside,
            "source": val["source"],
        })

    # Sort by signal priority
    signal_order = {
        "strong buy": 0, "buy": 1, "accumulate": 2, "accumulate on dips": 3,
        "hold": 4, "reduce": 5, "trim": 6, "exit": 7, "sell": 8,
    }

    def sort_key(r):
        sig = (r["signal"] or "").lower().strip()
        return (signal_order.get(sig, 4), r["symbol"])

    rows.sort(key=sort_key)

    # Print table
    print(f"\n# Comprehensive Valuation Summary — {len(rows)} companies with reports\n")
    print(f"| # | Symbol | Company | CMP | Bear | Base | Bull | Signal | Upside% |")
    print(f"|---|--------|---------|----:|-----:|-----:|-----:|--------|--------:|")

    for i, r in enumerate(rows, 1):
        cmp_s = f"₹{r['cmp']:.0f}" if r['cmp'] else "—"
        bear_s = f"₹{float(r['bear']):.0f}" if r['bear'] else "—"
        base_s = f"₹{float(r['base']):.0f}" if r['base'] else "—"
        bull_s = f"₹{float(r['bull']):.0f}" if r['bull'] else "—"
        up_s = f"{r['upside']}%" if r['upside'] is not None else "—"
        sig = r["signal"] or "—"
        print(f"| {i} | {r['symbol']} | {r['name']} | {cmp_s} | {bear_s} | {base_s} | {bull_s} | {sig} | {up_s} |")

    print(f"\n**Total with reports: {len(rows)}** | **No report: {len(no_report)}**\n")
    if no_report:
        print(f"Companies without valuation report: {', '.join(no_report)}")


if __name__ == "__main__":
    # Save to file
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main()
    output = buf.getvalue()
    out_path = ROOT / "portfolio" / "ZV3899" / "valuation_summary.md"
    out_path.write_text(output)
    print(f"Saved to {out_path}")
    print(output)
