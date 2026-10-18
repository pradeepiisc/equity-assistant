#!/usr/bin/env python3
"""Generate consolidated Dec 2025 thesis review report from all generated thesis_review.json files."""

import json
from pathlib import Path
from datetime import date

COMPANIES_DIR = Path("data/companies")
HOLDINGS_FILE = Path("portfolio/ZV3899/holdings/holdings.json")
QUARTER = "Mar 2026"
OUTPUT_FILE = Path("data/earnings_season/Mar2026/thesis_review_consolidated.md")

def main():
    # Load holdings
    holdings_map = {}
    if HOLDINGS_FILE.exists():
        holdings = json.loads(HOLDINGS_FILE.read_text())
        total_val = sum(h.get("current_value", 0) for h in holdings)
        for h in holdings:
            sym = h.get("symbol", "").upper()
            holdings_map[sym] = {
                "alloc": round(h.get("current_value", 0) / total_val * 100, 1) if total_val else 0,
                "pnl_pct": round(h.get("pnl_pct", 0), 1),
                "last_price": h.get("last_price", 0),
                "quantity": h.get("quantity", 0),
            }

    import re as _re

    # Build set of symbols with Mar 2026 as latest quarter
    mar26_symbols = set()
    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir():
            continue
        fin_files = sorted((d / "financials").glob(f"{d.name}_financials_*.txt"), reverse=True) if (d / "financials").exists() else []
        if not fin_files:
            continue
        text = fin_files[0].read_text()[:2000]
        m = _re.search(r"## Quarterly Results\s*\n", text)
        if not m:
            continue
        header = text[m.end():].split("\n")[0]
        quarters = [q.strip() for q in header.split("|")[1:] if q.strip()]
        if quarters and quarters[-1] == QUARTER:
            mar26_symbols.add(d.name.upper())

    print(f"Companies with {QUARTER} as latest quarter: {len(mar26_symbols)}")

    # Collect thesis reviews for Mar 2026 companies only
    reviews = []
    for d in sorted(COMPANIES_DIR.iterdir()):
        if not d.is_dir():
            continue
        if d.name.upper() not in mar26_symbols:
            continue
        jr = d / "reports" / "thesis_review.json"
        if not jr.exists():
            continue
        try:
            r = json.loads(jr.read_text())
            meta = d / "meta.json"
            name = d.name
            if meta.exists():
                name = json.loads(meta.read_text()).get("name", d.name)
            reviews.append({
                "symbol": d.name.upper(),
                "name": name,
                "review": r,
                "held": d.name.upper() in holdings_map,
                "alloc": holdings_map.get(d.name.upper(), {}).get("alloc", 0),
                "pnl_pct": holdings_map.get(d.name.upper(), {}).get("pnl_pct", 0),
            })
        except Exception:
            pass

    print(f"Loaded {len(reviews)} thesis reviews")

    # Categorize
    strengthened = [r for r in reviews if r["review"].get("thesis_status") == "strengthened"]
    intact = [r for r in reviews if r["review"].get("thesis_status") == "intact"]
    weakening = [r for r in reviews if r["review"].get("thesis_status") == "weakening"]
    broken = [r for r in reviews if r["review"].get("thesis_status") == "broken"]

    print(f"Strengthened: {len(strengthened)}, Intact: {len(intact)}, Weakening: {len(weakening)}, Broken: {len(broken)}")

    lines = []
    lines.append(f"# Thesis Review — Consolidated Report ({QUARTER} Quarter)")
    lines.append(f"*Generated: {date.today().isoformat()} | Companies reviewed: {len(reviews)}*")
    lines.append("")
    lines.append(f"> **Purpose:** This report compares the latest {QUARTER} quarterly results")
    lines.append("> against the existing investment thesis from master reports generated in previous quarters.")
    lines.append("> Actionable recommendations for portfolio rebalancing based on latest earnings.")
    lines.append("")

    # Executive summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append("| Category | Count | % |")
    lines.append("|---|---|---|")
    for label, emoji, items in [
        ("Strengthened", "🟢", strengthened),
        ("Intact", "🔵", intact),
        ("Weakening", "🟠", weakening),
        ("Broken", "🔴", broken),
    ]:
        pct = len(items) / len(reviews) * 100 if reviews else 0
        lines.append(f"| {emoji} {label} | {len(items)} | {pct:.0f}% |")
    lines.append("")

    # Master table sorted by status then allocation
    lines.append("## Full Company Review Table")
    lines.append("")
    lines.append("| # | Company | Symbol | Status | Action | Score | Alloc% | P&L% | Summary |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    status_order = {"broken": 0, "weakening": 1, "intact": 2, "strengthened": 3}
    sorted_reviews = sorted(
        reviews,
        key=lambda r: (status_order.get(r["review"].get("thesis_status", ""), 2), -r["alloc"]),
    )

    for i, r in enumerate(sorted_reviews, 1):
        rv = r["review"]
        status = rv.get("thesis_status", "?")
        emoji = {"strengthened": "🟢", "intact": "🔵", "weakening": "🟠", "broken": "🔴"}.get(status, "⚪")
        conv = rv.get("updated_conviction", {})
        action = conv.get("updated_action", "?")
        score = conv.get("score", "?")
        summary = rv.get("one_line_summary", "")[:80]
        alloc = f"{r['alloc']:.1f}" if r["held"] else "—"
        pnl = f"{r['pnl_pct']:.0f}%" if r["held"] else "—"
        lines.append(f"| {i} | {r['name'][:30]} | {r['symbol']} | {emoji} {status} | {action} | {score}/10 | {alloc} | {pnl} | {summary} |")

    lines.append("")

    # Detailed sections by category
    for category, emoji, label, items in [
        ("strengthened", "🟢", "THESIS STRENGTHENED — Consider Adding", strengthened),
        ("broken", "🔴", "THESIS BROKEN — Consider Exiting", broken),
        ("weakening", "🟠", "THESIS WEAKENING — Watch / Trim", weakening),
        ("intact", "🔵", "THESIS INTACT — Hold", intact),
    ]:
        if not items:
            continue
        lines.append(f"## {emoji} {label} ({len(items)} companies)")
        lines.append("")

        items_sorted = sorted(items, key=lambda r: -r["alloc"])

        for r in items_sorted:
            rv = r["review"]
            conv = rv.get("updated_conviction", {})
            lines.append(f"### {r['name']} ({r['symbol']})")
            alloc_str = f" | Portfolio: {r['alloc']:.1f}%" if r["held"] else " | Not held"
            pnl_str = f" | P&L: {r['pnl_pct']:.0f}%" if r["held"] else ""
            lines.append(f"**{conv.get('updated_action', '?')}** (conviction {conv.get('score', '?')}/10){alloc_str}{pnl_str}")

            if summary := rv.get("one_line_summary"):
                lines.append(f"> {summary}")

            # Results assessment
            ra = rv.get("results_assessment", {})
            if ra:
                lines.append(f"- **Revenue:** {ra.get('revenue_vs_expectation', '?')[:60]}")
                lines.append(f"- **Margins:** {ra.get('margin_trend', '?')[:60]}")
                lines.append(f"- **Profit:** {ra.get('profit_growth', '?')[:60]}")

            # Key thesis delta
            td = rv.get("thesis_delta", {})
            if td.get("new_positives"):
                lines.append(f"- ✅ {td['new_positives'][0][:80]}")
            if td.get("at_risk_assumptions"):
                lines.append(f"- ⚠️ {td['at_risk_assumptions'][0][:80]}")
            if td.get("new_negatives"):
                lines.append(f"- 🔴 {td['new_negatives'][0][:80]}")

            # Price levels
            pl = conv.get("price_levels", {})
            if pl:
                parts = []
                if pl.get("add_below"):
                    parts.append(f"Add <₹{str(pl['add_below']).lstrip('₹')}")
                if pl.get("trim_above"):
                    parts.append(f"Trim >₹{str(pl['trim_above']).lstrip('₹')}")
                if pl.get("stop_loss"):
                    parts.append(f"SL ₹{str(pl['stop_loss']).lstrip('₹')}")
                if parts:
                    lines.append(f"- **Levels:** {' | '.join(parts)}")

            # Rationale
            if rationale := conv.get("action_rationale"):
                lines.append(f"- **Why:** {rationale[:150]}")

            lines.append("")

    # Action items summary
    lines.append("## Action Items Summary")
    lines.append("")
    add_items = [r for r in reviews if r["review"].get("updated_conviction", {}).get("updated_action", "").lower() in ("add", "accumulate", "buy")]
    trim_items = [r for r in reviews if r["review"].get("updated_conviction", {}).get("updated_action", "").lower() in ("trim", "partial exit")]
    exit_items = [r for r in reviews if r["review"].get("updated_conviction", {}).get("updated_action", "").lower() in ("exit", "sell")]
    hold_items = [r for r in reviews if r["review"].get("updated_conviction", {}).get("updated_action", "").lower() == "hold"]

    if add_items:
        names = ", ".join(
            f"{r['symbol']}({r['alloc']:.1f}%)" if r["held"] else r["symbol"]
            for r in sorted(add_items, key=lambda x: -x["alloc"])
        )
        lines.append(f"**🟢 ADD ({len(add_items)}):** {names}")
        lines.append("")
    if trim_items:
        names = ", ".join(
            f"{r['symbol']}({r['alloc']:.1f}%)" if r["held"] else r["symbol"]
            for r in sorted(trim_items, key=lambda x: -x["alloc"])
        )
        lines.append(f"**🟠 TRIM ({len(trim_items)}):** {names}")
        lines.append("")
    if exit_items:
        names = ", ".join(
            f"{r['symbol']}({r['alloc']:.1f}%)" if r["held"] else r["symbol"]
            for r in sorted(exit_items, key=lambda x: -x["alloc"])
        )
        lines.append(f"**🔴 EXIT ({len(exit_items)}):** {names}")
        lines.append("")
    if hold_items:
        names = ", ".join(
            f"{r['symbol']}" for r in sorted(hold_items, key=lambda x: -x["alloc"])
        )
        lines.append(f"**🔵 HOLD ({len(hold_items)}):** {names}")
        lines.append("")

    lines.append("---")
    lines.append("*Per-company detailed reports: data/companies/{SYMBOL}/reports/thesis_review.md*")

    report = "\n".join(lines)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report, encoding="utf-8")
    print(f"\nSaved to {OUTPUT_FILE} ({len(report)} chars, {len(lines)} lines)")


if __name__ == "__main__":
    main()
