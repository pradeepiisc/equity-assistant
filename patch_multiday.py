import re

with open("workflows/portfolio_daily_report.py", "r") as f:
    content = f.read()

new_func = '''def _build_multiday_movement_section(
    holdings: list[dict],
    multiday_prices: dict[str, dict[str, float]],
) -> list[str]:
    """Build multi-day price movement table sorted by total % change (fallers first)."""
    from workflows.shared_sections import is_sme
    
    lines: list[str] = []
    dates = sorted(multiday_prices.keys())
    if len(dates) < 2:
        lines.append("## Multi-Day Price Movement")
        lines.append("*Not enough historical snapshots — need at least 2 days of data.*\n")
        return lines

    def _md(d: str) -> str:
        _, m, day = d.split("-")
        return f"{m}-{day}"

    day_pairs = [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]
    col_headers = [f"{_md(a)}→{_md(b)}" for a, b in day_pairs]

    rows_mainboard = []
    rows_sme = []
    
    total_portfolio_alloc = sum(h["pct_of_portfolio"] for h in holdings)
    sme_alloc = 0.0
    mainboard_alloc = 0.0

    for h in holdings:
        sym = h["symbol"]
        is_curr_sme = is_sme(sym, h.get("exchange", ""))
        
        if is_curr_sme:
            sme_alloc += h["pct_of_portfolio"]
        else:
            mainboard_alloc += h["pct_of_portfolio"]
            
        day_changes = []
        for d_from, d_to in day_pairs:
            p0 = multiday_prices[d_from].get(sym)
            p1 = multiday_prices[d_to].get(sym)
            day_changes.append((p1 - p0) / p0 * 100 if p0 and p1 and p0 > 0 else None)
            
        p_first = multiday_prices[dates[0]].get(sym)
        p_last  = multiday_prices[dates[-1]].get(sym)
        total_pct = (p_last - p_first) / p_first * 100 if p_first and p_last and p_first > 0 else None
        
        row_data = {
            "symbol": sym,
            "day_changes": day_changes,
            "total_pct": total_pct,
            "pct_of_portfolio": h["pct_of_portfolio"],
            "last_price": h["last_price"],
        }
        
        if is_curr_sme:
            rows_sme.append(row_data)
        else:
            rows_mainboard.append(row_data)

    def _cell(v: float | None) -> str:
        if v is None:
            return "—"
        arrow = "📈" if v > 0.5 else ("📉" if v < -0.5 else "➡️")
        return f"{arrow} {'+' if v >= 0 else ''}{v:.1f}%"

    n = len(dates)
    
    # Helper to render a section
    def _render_table(title, rows, alloc_pct):
        r_lines = []
        r_lines.append(f"## {title} ({n} days: {_md(dates[0])} → {_md(dates[-1])})")
        r_lines.append(f"*{alloc_pct:.1f}% of Portfolio Value · sorted by total move (fallers first)*\\n")
        r_lines.append("| Stock | LTP | Alloc% | " + " | ".join(col_headers) + " | Total |")
        r_lines.append("|---|---|---|" + "|".join(["---"] * len(col_headers)) + "|---|")
        
        r_with_total = sorted([r for r in rows if r["total_pct"] is not None], key=lambda x: x["total_pct"])
        r_no_data    = [r for r in rows if r["total_pct"] is None]
        
        for r in r_with_total + r_no_data:
            cells = " | ".join(_cell(c) for c in r["day_changes"])
            r_lines.append(f"| {r['symbol']} | ₹{r['last_price']:.1f} | {r['pct_of_portfolio']:.1f}% | {cells} | {_cell(r['total_pct'])} |")
        
        r_lines.append("")
        
        c_falling = [r for r in r_with_total if all(c is not None and c < 0 for c in r["day_changes"])]
        c_rising  = [r for r in r_with_total if all(c is not None and c > 0 for c in r["day_changes"])]
        strict_syms = {r["symbol"] for r in c_rising + c_falling}
        
        s_rising = [r for r in r_with_total if r["symbol"] not in strict_syms and r["total_pct"] > 1.0 and all(c is not None and c > -1.0 for c in r["day_changes"])]
        s_falling = [r for r in r_with_total if r["symbol"] not in strict_syms and r["total_pct"] < -1.0 and all(c is not None and c < 1.0 for c in r["day_changes"])]
        
        if c_falling or c_rising or s_rising or s_falling:
            r_lines.append(f"### {title.split(' ')[0]} Trend Summary")
            if c_falling:
                r_lines.append(f"📉 **Consistently falling** ({len(c_falling)} stocks): {', '.join(r['symbol'] for r in c_falling)}")
            if s_falling:
                r_lines.append(f"🔻 **Soft falling** (trend down >1%, dips <1% bounce, {len(s_falling)} stocks): {', '.join(r['symbol'] for r in s_falling[:10])}{' …' if len(s_falling) > 10 else ''}")
            if c_rising:
                r_lines.append(f"📈 **Consistently rising** ({len(c_rising)} stocks): {', '.join(r['symbol'] for r in c_rising)}")
            if s_rising:
                r_lines.append(f"🔺 **Soft rising** (trend up >1%, dips <1%, {len(s_rising)} stocks): {', '.join(r['symbol'] for r in s_rising[:10])}{' …' if len(s_rising) > 10 else ''}")
            r_lines.append("")
            
        return r_lines

    lines.extend(_render_table("Mainboard Multi-Day Price Movement", rows_mainboard, mainboard_alloc))
    lines.extend(_render_table("SME Multi-Day Price Movement", rows_sme, sme_alloc))
    
    # Bottom SME summary
    lines.append("## SME Portfolio Exposure")
    lines.append(f"**Total SME Allocation:** {sme_alloc:.2f}% of present value")
    lines.append(f"**Total Mainboard Allocation:** {mainboard_alloc:.2f}% of present value")
    lines.append("")

    return lines
'''

start_idx = content.find("def _build_multiday_movement_section(")
end_idx = content.find("def build_report(", start_idx)

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_func + "\n" + content[end_idx:]
    with open("workflows/portfolio_daily_report.py", "w") as f:
        f.write(new_content)
    print("Patched successfully")
else:
    print("Could not find function bounds")
