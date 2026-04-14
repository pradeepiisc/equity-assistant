"""
Generate valuation_summary.md — a comprehensive table of all companies
with their valuation ranges, signals, and ratings from master_report.json
and valuation_agent.json.
"""
from __future__ import annotations
import json
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "companies"
PORTFOLIO = ROOT / "portfolio" / "ZV3899"
SKIP_FOLDERS = {"543619", "544091", "544213", "544458", "544681"}


def load_all_companies() -> dict[str, dict]:
    """Load all companies from both YAMLs, keyed by symbol."""
    companies = {}
    for yf in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = ROOT / yf
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            for c in data.get("stocks", []):
                sym = c.get("symbol", "").upper()
                if sym:
                    companies[sym] = c
    return companies


def main():
    all_companies = load_all_companies()
    rows = []

    for d in sorted(DATA.iterdir()):
        sym = d.name
        if sym in SKIP_FOLDERS or not d.is_dir():
            continue

        reports_dir = d / "reports"
        va_json = reports_dir / "valuation_agent.json"
        mr_json = reports_dir / "master_report.json"

        company_info = all_companies.get(sym, {})
        name = company_info.get("name", sym)
        sector = company_info.get("sector", "")

        # Try to get price from company info
        price = ""
        if company_info.get("last_price"):
            price = f"₹{company_info['last_price']}"

        # Read valuation agent data
        fv_low = ""
        fv_high = ""
        signal = ""
        upside = ""

        if va_json.exists():
            try:
                va = json.loads(va_json.read_text())
                fv_low = va.get("fair_value_low", "")
                fv_high = va.get("fair_value_high", "")
                signal = va.get("entry_signal", "")

                # Get price from valuation JSON (most reliable)
                cp = va.get("current_price")
                if cp:
                    try:
                        price = f"₹{float(cp):.0f}"
                    except (ValueError, TypeError):
                        pass

                # Get upside from valuation JSON
                up = va.get("upside_pct")
                if up is not None:
                    try:
                        upside = f"{float(up):+.0f}%"
                    except (ValueError, TypeError):
                        pass

                # Fallback: compute upside if not in JSON
                if not upside and cp and fv_high and fv_low:
                    try:
                        p = float(str(cp))
                        fh = float(str(fv_high))
                        fl = float(str(fv_low))
                        mid = (fl + fh) / 2
                        upside = f"{((mid - p) / p) * 100:+.0f}%"
                    except (ValueError, ZeroDivisionError):
                        pass
            except (json.JSONDecodeError, Exception):
                pass

        # Read master report data
        rating = ""
        if mr_json.exists():
            try:
                mr = json.loads(mr_json.read_text())
                rating = mr.get("overall_rating", "") or mr.get("rating", "") or mr.get("action_recommendation", "")
            except (json.JSONDecodeError, Exception):
                pass

        # Format fair value range
        fv_range = ""
        if fv_low and fv_high:
            fv_range = f"₹{fv_low}–₹{fv_high}"

        has_report = (reports_dir / "master_report.md").exists()

        rows.append({
            "symbol": sym,
            "name": name,
            "sector": sector,
            "price": price,
            "fv_range": fv_range,
            "signal": signal,
            "rating": rating,
            "upside": upside,
            "has_report": has_report,
        })

    # Sort by signal priority then symbol
    signal_order = {
        "Buy Now": 0,
        "Accumulate on Dips": 1,
        "Accumulate": 1,
        "Hold": 2,
        "Trim": 3,
        "Exit": 4,
        "": 5,
    }

    rows.sort(key=lambda r: (signal_order.get(r["signal"], 3), r["symbol"]))

    # Generate markdown
    lines = []
    lines.append("# Portfolio Valuation Summary")
    lines.append(f"\n*Generated from {len(rows)} companies with reports*\n")

    # Count stats
    with_reports = sum(1 for r in rows if r["has_report"])
    with_valuation = sum(1 for r in rows if r["fv_range"])
    lines.append(f"- **Companies with reports:** {with_reports}")
    lines.append(f"- **Companies with valuations:** {with_valuation}")
    lines.append("")

    # Signal distribution
    from collections import Counter
    sig_counts = Counter(r["signal"] for r in rows if r["signal"])
    if sig_counts:
        lines.append("## Signal Distribution\n")
        for sig, cnt in sorted(sig_counts.items(), key=lambda x: signal_order.get(x[0], 3)):
            lines.append(f"- **{sig}:** {cnt}")
        lines.append("")

    # Main table
    lines.append("## Valuation Table\n")
    lines.append("| Symbol | Company | Sector | Price | Fair Value | Signal | Upside | Rating |")
    lines.append("|--------|---------|--------|------:|------------|--------|-------:|--------|")

    for r in rows:
        if not r["has_report"]:
            continue
        sym = r["symbol"]
        name = r["name"][:30]
        sector = r["sector"][:25] if r["sector"] else ""
        price = r["price"] or "–"
        fv = r["fv_range"] or "–"
        sig = r["signal"] or "–"
        upside = r["upside"] or "–"
        rating = r["rating"] or "–"
        lines.append(f"| {sym} | {name} | {sector} | {price} | {fv} | {sig} | {upside} | {rating} |")

    # Companies without reports
    no_report = [r for r in rows if not r["has_report"]]
    if no_report:
        lines.append(f"\n## Companies Without Reports ({len(no_report)})\n")
        for r in no_report:
            lines.append(f"- {r['symbol']} ({r['name']})")

    md_text = "\n".join(lines) + "\n"

    # Save to portfolio folder
    out_path = PORTFOLIO / "valuation_summary.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_text, encoding="utf-8")
    print(f"Saved → {out_path}")
    print(f"Total rows: {len(rows)} | With reports: {with_reports} | With valuations: {with_valuation}")


if __name__ == "__main__":
    main()
