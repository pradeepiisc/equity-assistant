"""
Shared section builders for portfolio reports.
===============================================
Reusable markdown section generators used by both:
  - workflows/earnings_season.py  (quarterly evolving files)
  - workflows/portfolio_daily_report.py  (daily evolving files)

Each render_* function returns a list of markdown lines.
compute_dma_buckets() is the common prerequisite for several renderers.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"

SME_SUFFIXES = ("-SM", "-BE", "-SME")
DEPLOY_ALLOC_THRESH = 2.0
CAUTION_ALLOC_THRESH = 0.5


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_sme(symbol: str, exchange: str = "") -> bool:
    """Heuristic: SME if symbol has -SM/-BE suffix or BSE-only numeric code."""
    sym_upper = symbol.upper()
    if any(sym_upper.endswith(s) for s in SME_SUFFIXES):
        return True
    if exchange.upper() == "BSE" and symbol.isdigit():
        return True
    return False


def classify_trend(
    price: float,
    dma40: float | None,
    dma100: float | None,
) -> tuple[str, str]:
    """Classify trend based on price vs DMA40 and DMA100.
    Returns (emoji, label)."""
    if dma40 is None or dma100 is None:
        return "⚪", "No data"
    if price > dma40 and dma40 > dma100:
        return "🟢", "Strong Bullish"
    if price > dma100 and price <= dma40:
        return "🔵", "Bullish Pullback"
    if abs(price - dma100) / dma100 * 100 < 3:
        return "🟡", "Neutral"
    if price < dma100 and price >= dma40:
        return "🟠", "Bearish Rally"
    if price < dma100:
        return "🔴", "Bearish"
    return "🟡", "Neutral"


def inr(amount: float, sign: bool = False) -> str:
    neg = amount < 0
    s = f"₹{abs(amount):,.0f}"
    if sign:
        return f"{'−' if neg else '+'}{s}"
    return f"{'−' if neg else ''}{s}"


def pct(v: float, sign: bool = True) -> str:
    return f"{'+' if (sign and v >= 0) else ''}{v:.1f}%"


# ── DMA Deployment ───────────────────────────────────────────────────────────

def compute_dma_buckets(
    holdings: list[dict],
    dma_data: dict[str, dict],
    company_map: dict[str, dict],
    watchlist_symbols: set[str],
) -> dict[str, list[dict]]:
    """Compute DMA trend buckets for all holdings.
    Returns {label: [entry_dicts]} sorted by allocation within each bucket."""
    total_value = sum(h.get("current_value", 0) for h in holdings)

    buckets: dict[str, list] = {
        "Strong Bullish": [], "Bullish Pullback": [], "Neutral": [],
        "Bearish Rally": [], "Bearish": [], "No data": [],
    }
    for h in sorted(holdings, key=lambda x: -x.get("current_value", 0)):
        sym = h["symbol"]
        d = dma_data.get(sym, {})
        dma40 = d.get("dma40")
        dma100 = d.get("dma100")
        ltp = h.get("last_price", 0)
        alloc = round(h["current_value"] / total_value * 100, 2) if total_value else 0
        emoji, label = classify_trend(ltp, dma40, dma100)
        c = company_map.get(sym, {})
        sme_flag = is_sme(sym, c.get("exchange", ""))
        entry = {
            "symbol": sym, "ltp": ltp, "dma40": dma40, "dma100": dma100,
            "trend": label, "emoji": emoji, "alloc": alloc,
            "pnl_pct": h.get("pnl_pct", 0), "sme": sme_flag,
            "sector": c.get("sector", "—"),
            "is_watchlist": sym in watchlist_symbols,
        }
        buckets[label].append(entry)
    return buckets


def render_dma_deployment(buckets: dict[str, list[dict]]) -> list[str]:
    """Render DMA deployment analysis markdown section."""
    lines = [
        "## 📈 DMA Deployment Analysis",
        "*DMA40 + DMA100 trend for all holdings — grouped by trend, sorted by allocation*\n",
    ]

    for label in ["Strong Bullish", "Bullish Pullback", "Neutral", "Bearish Rally", "Bearish"]:
        items = buckets[label]
        if items:
            total_alloc = sum(e["alloc"] for e in items)
            emoji = items[0]["emoji"]
            lines.append(
                f"- {emoji} **{label}**: {len(items)} stocks, {total_alloc:.1f}% of portfolio"
            )
    lines.append("")

    lines.append("| # | Stock | LTP | DMA40 | DMA100 | Trend | Alloc% | P&L% | SME | Sector |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    rank = 0
    for label in ["Strong Bullish", "Bullish Pullback", "Neutral", "Bearish Rally", "Bearish", "No data"]:
        items = buckets[label]
        if not items:
            continue
        for entry in sorted(items, key=lambda x: -x["alloc"]):
            rank += 1
            dma40_str = f"₹{entry['dma40']:.0f}" if entry["dma40"] else "—"
            dma100_str = f"₹{entry['dma100']:.0f}" if entry["dma100"] else "—"
            sme_str = "🏷️" if entry["sme"] else ""
            wl = " ★" if entry["is_watchlist"] else ""
            lines.append(
                f"| {rank} | {entry['symbol']}{wl} | ₹{entry['ltp']:.1f} "
                f"| {dma40_str} | {dma100_str} "
                f"| {entry['emoji']} {entry['trend']} | {entry['alloc']:.1f}% "
                f"| {pct(entry['pnl_pct'])} | {sme_str} | {entry['sector'][:30]} |"
            )
    lines.append("\n*★ = watchlist stock*\n")
    return lines


# ── Sector Allocation ────────────────────────────────────────────────────────

def render_sector_allocation(
    held_symbols: list[str],
    holdings_map: dict[str, dict],
    dma_data: dict[str, dict],
    company_map: dict[str, dict],
) -> list[str]:
    """Render sector allocation markdown section."""
    lines = ["## 🏢 Sector Allocation"]
    sector_alloc: dict[str, float] = {}
    sector_stocks: dict[str, list[str]] = {}
    sector_trend: dict[str, dict[str, int]] = {}

    total_value = sum(holdings_map[s].get("current_value", 0) for s in held_symbols if s in holdings_map)

    for sym in held_symbols:
        if sym not in holdings_map:
            continue
        c = company_map.get(sym, {})
        sector = c.get("sector", "Unknown") or "Unknown"
        sector_key = sector.split(" - ")[0].strip() if " - " in sector else sector
        h = holdings_map[sym]
        alloc_val = round(h["current_value"] / total_value * 100, 2) if total_value else 0
        sector_alloc[sector_key] = sector_alloc.get(sector_key, 0) + alloc_val
        sector_stocks.setdefault(sector_key, []).append(sym)
        d = dma_data.get(sym, {})
        _, label = classify_trend(h.get("last_price", 0), d.get("dma40"), d.get("dma100"))
        bucket = sector_trend.setdefault(sector_key, {"bullish": 0, "bearish": 0, "neutral": 0, "nodata": 0})
        if label == "No data":
            bucket["nodata"] += 1
        elif "Bullish" in label:
            bucket["bullish"] += 1
        elif "Bearish" in label:
            bucket["bearish"] += 1
        else:
            bucket["neutral"] += 1

    lines.append("| Sector | Alloc% | Stocks | Trend Mix |")
    lines.append("|---|---|---|---|")
    for sector in sorted(sector_alloc, key=lambda s: -sector_alloc[s]):
        alloc_val = sector_alloc[sector]
        stocks = sector_stocks[sector]
        stock_list = ", ".join(stocks[:4])
        if len(stocks) > 4:
            stock_list += f" +{len(stocks) - 4}"
        t = sector_trend.get(sector, {})
        trend_str = ""
        if t.get("bullish"):
            trend_str += f"🟢{t['bullish']} "
        if t.get("bearish"):
            trend_str += f"🔴{t['bearish']} "
        if t.get("neutral"):
            trend_str += f"🟡{t['neutral']} "
        if t.get("nodata"):
            trend_str += f"⚪{t['nodata']}"
        lines.append(f"| {sector} | {alloc_val:.1f}% | {stock_list} | {trend_str.strip()} |")
    lines.append("")
    return lines


# ── Deployment Candidates ────────────────────────────────────────────────────

def render_deployment_candidates(
    buckets: dict[str, list[dict]],
    threshold: float = DEPLOY_ALLOC_THRESH,
) -> list[str]:
    """Render deployment candidates markdown section."""
    lines = [
        "## 🎯 Deployment Candidates",
        f"*Bullish trend + allocation < {threshold:.0f}% — consider increasing position*\n",
    ]
    candidates = []
    for label in ["Strong Bullish", "Bullish Pullback"]:
        for e in buckets[label]:
            if e["alloc"] < threshold:
                candidates.append(e)
    if candidates:
        candidates.sort(key=lambda x: x["alloc"])
        lines.append("| Stock | Trend | Alloc% | LTP | DMA40 | DMA100 | Sector | SME |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for e in candidates[:25]:
            dma40_str = f"₹{e['dma40']:.0f}" if e["dma40"] else "—"
            dma100_str = f"₹{e['dma100']:.0f}" if e["dma100"] else "—"
            sme_str = "🏷️" if e["sme"] else ""
            lines.append(
                f"| {e['symbol']} | {e['emoji']} {e['trend']} | {e['alloc']:.1f}% "
                f"| ₹{e['ltp']:.1f} | {dma40_str} | {dma100_str} "
                f"| {e['sector'][:25]} | {sme_str} |"
            )
    else:
        lines.append("*No under-allocated bullish stocks found.*")
    lines.append("")
    return lines


# ── Caution List ─────────────────────────────────────────────────────────────

def render_caution_list(
    buckets: dict[str, list[dict]],
    threshold: float = CAUTION_ALLOC_THRESH,
) -> list[str]:
    """Render caution list markdown section."""
    lines = [
        "## ⚠️ Caution List",
        f"*Bearish trend + allocation ≥ {threshold:.1f}% — consider reducing*\n",
    ]
    caution = []
    for label in ["Bearish", "Bearish Rally"]:
        for e in buckets[label]:
            if e["alloc"] >= threshold:
                caution.append(e)
    if caution:
        caution.sort(key=lambda x: -x["alloc"])
        lines.append("| Stock | Trend | Alloc% | LTP | DMA100 | P&L% | Sector |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in caution[:25]:
            dma100_str = f"₹{e['dma100']:.0f}" if e["dma100"] else "—"
            lines.append(
                f"| {e['symbol']} | {e['emoji']} {e['trend']} | {e['alloc']:.1f}% "
                f"| ₹{e['ltp']:.1f} | {dma100_str} "
                f"| {pct(e['pnl_pct'])} | {e['sector'][:25]} |"
            )
    else:
        lines.append("✅ No high-allocation bearish stocks found.")
    lines.append("")
    return lines


# ── Watchlist — Not Yet Held ─────────────────────────────────────────────────

def render_watchlist_not_held(
    watchlist_symbols: set[str],
    holdings_map: dict[str, dict],
    dma_data: dict[str, dict],
    company_map: dict[str, dict],
) -> list[str]:
    """Render watchlist not-held markdown section."""
    wl_not_held = [
        sym for sym in watchlist_symbols
        if sym not in holdings_map and sym in dma_data
    ]
    if not wl_not_held:
        return []
    lines = [
        "## 👀 Watchlist — Not Yet Held",
        "*DMA status for watchlist stocks you don't own yet*\n",
        "| Stock | LTP | DMA40 | DMA100 | Trend | Sector |",
        "|---|---|---|---|---|---|",
    ]
    for sym in sorted(wl_not_held):
        d = dma_data.get(sym, {})
        ltp = d.get("last_price", 0)
        dma40 = d.get("dma40")
        dma100 = d.get("dma100")
        emoji, label = classify_trend(ltp or 0, dma40, dma100)
        c = company_map.get(sym, {})
        dma40_str = f"₹{dma40:.0f}" if dma40 else "—"
        dma100_str = f"₹{dma100:.0f}" if dma100 else "—"
        ltp_str = f"₹{ltp:.1f}" if ltp else "—"
        lines.append(
            f"| {sym} | {ltp_str} | {dma40_str} | {dma100_str} "
            f"| {emoji} {label} | {c.get('sector', '—')[:30]} |"
        )
    lines.append("")
    return lines


# ── SME Holdings Summary ─────────────────────────────────────────────────────

def render_sme_summary(
    holdings: list[dict],
    dma_data: dict[str, dict],
    company_map: dict[str, dict],
) -> list[str]:
    """Render SME holdings summary markdown section."""
    sme_holdings = [
        h for h in holdings
        if is_sme(h["symbol"], company_map.get(h["symbol"], {}).get("exchange", ""))
    ]
    if not sme_holdings:
        return []

    total_value = sum(h.get("current_value", 0) for h in holdings)
    sme_alloc = sum(
        round(h["current_value"] / total_value * 100, 2) if total_value else 0
        for h in sme_holdings
    )
    lines = [
        "## 🏷️ SME Holdings Summary",
        f"*{len(sme_holdings)} SME stocks · total allocation: {sme_alloc:.1f}%*\n",
        "| Stock | Alloc% | P&L% | LTP | Trend |",
        "|---|---|---|---|---|",
    ]
    for h in sorted(sme_holdings, key=lambda x: -x.get("current_value", 0)):
        alloc_val = round(h["current_value"] / total_value * 100, 2) if total_value else 0
        d = dma_data.get(h["symbol"], {})
        emoji, label = classify_trend(h.get("last_price", 0), d.get("dma40"), d.get("dma100"))
        lines.append(
            f"| {h['symbol']} | {alloc_val:.1f}% "
            f"| {pct(h.get('pnl_pct', 0))} | ₹{h.get('last_price', 0):.1f} "
            f"| {emoji} {label} |"
        )
    lines.append("")
    return lines


# ── Portfolio Concentration (Cumulative Coverage) ────────────────────────────

def render_portfolio_concentration(
    holdings: list[dict],
    watchlist_symbols: set[str] | None = None,
) -> list[str]:
    """Render cumulative portfolio coverage table sorted by value."""
    watchlist_symbols = watchlist_symbols or set()
    lines: list[str] = [
        "## Portfolio Concentration (Cumulative Coverage)",
        "*Sorted by current value ↓ — shows how many stocks make up 50/70/90% of the portfolio*\n",
        "| # | Stock | LTP | Alloc% | Cumulative% | P&L% |",
        "|---|---|---|---|---|---|",
    ]
    by_value = sorted(holdings, key=lambda x: -x.get("current_value", 0))
    cumulative = 0.0
    milestones = {50, 70, 90}
    crossed: set[int] = set()
    for i, h in enumerate(by_value, 1):
        cumulative += h.get("pct_of_portfolio", 0.0)
        flag = " ★" if h.get("symbol", "").upper() in watchlist_symbols or h.get("pct_of_portfolio", 0.0) >= 1.0 else ""
        lines.append(
            f"| {i} | {h['symbol']}{flag} | ₹{h.get('last_price', 0):.1f} | {h.get('pct_of_portfolio', 0.0):.1f}% "
            f"| **{cumulative:.1f}%** | {pct(h.get('pnl_pct', 0.0))} |"
        )
        for m in sorted(milestones - crossed):
            if cumulative >= m:
                lines.append(f"| | ↑ **{i} stocks = {m}% of portfolio** | | | | |")
                crossed.add(m)
    lines.append("")
    return lines


# ── Write all daily evolving files ───────────────────────────────────────────

def write_daily_evolving_files(
    user_dir: Path,
    holdings: list[dict],
    dma_data: dict[str, dict],
    company_map: dict[str, dict],
    watchlist_symbols: set[str],
) -> list[Path]:
    """Generate and save all daily evolving section files to user_dir/.
    Returns list of paths written."""
    from datetime import date as _date

    holdings_map = {h["symbol"]: h for h in holdings}
    held_symbols = [h["symbol"] for h in sorted(holdings, key=lambda x: -x.get("current_value", 0))]
    today = _date.today().isoformat()
    header = f"*Updated: {today}*\n"
    written: list[Path] = []

    buckets = compute_dma_buckets(holdings, dma_data, company_map, watchlist_symbols)

    sections = {
        "dma_deployment.md": render_dma_deployment(buckets),
        "sector_allocation.md": render_sector_allocation(held_symbols, holdings_map, dma_data, company_map),
        "deployment_candidates.md": render_deployment_candidates(buckets),
        "caution_list.md": render_caution_list(buckets),
        "watchlist_not_held.md": render_watchlist_not_held(watchlist_symbols, holdings_map, dma_data, company_map),
        "sme_summary.md": render_sme_summary(holdings, dma_data, company_map),
        "portfolio_concentration.md": render_portfolio_concentration(holdings, watchlist_symbols),
    }

    for filename, section_lines in sections.items():
        if not section_lines:
            continue
        out = user_dir / filename
        content = header + "\n".join(section_lines)
        out.write_text(content, encoding="utf-8")
        written.append(out)

    return written
