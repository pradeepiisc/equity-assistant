"""
Workflow: Daily Portfolio Health Report
========================================
Reads the latest Kite portfolio snapshot and produces a daily health report:

  1. Overview — invested, current value, total P&L
  2. Trades Today — buys and sells from positions.json
  3. Position-sizing alerts — any single stock ≥ 5% of portfolio
  4. DMA status — below 99 DMA (reduce signal) / above 99 DMA (avg-up candidates)
  5. Portfolio Concentration — all stocks sorted by value with 50/70/90% milestones
  6. Top 10 Daily Movers — day % change vs previous session close
  7. Watchlist stocks currently held

Usage:
    python -m workflows.portfolio_daily_report              # full run with 99 DMA
    python -m workflows.portfolio_daily_report --no-dma     # skip DMA fetch (faster)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
CACHE_DIR = PORTFOLIO_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_POSITION_PCT = 5.0   # alert if any stock ≥ this %
WARN_POSITION_PCT = 4.0  # yellow zone
DMA_STRONG_ABOVE_PCT = 10.0  # flag averaging-up if >10% above 99 DMA
MAX_DAY_MOVER_GAP_DAYS = 3   # allow Fri->Mon, block older stale comparisons
MULTIDAY_LOOKBACK = 5        # days of snapshots to use in multi-day movement section
TOP_CHART_COUNT = 10
CHART_LOOKBACK_DAYS = 1095


# ── data loading ──────────────────────────────────────────────────────────────

def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio data found. Run: python -m integrations.kite_connect --save-portfolio")
    if user_id:
        match = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
        if not match:
            raise FileNotFoundError(f"User '{user_id}' not found under portfolio/")
        return match
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users found: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_latest_holdings(user_dir: Path) -> tuple[list[dict], str]:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings/ directory found under {user_dir}. Run: python -m integrations.kite_connect --save-portfolio")
    date_dirs = sorted(
        [d for d in holdings_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    if not date_dirs:
        raise FileNotFoundError(f"No dated snapshots found under {holdings_dir}")
    latest = date_dirs[0]
    holdings_file = latest / "holdings.json"
    if not holdings_file.exists():
        raise FileNotFoundError(f"holdings.json missing in {latest}")
    with open(holdings_file) as f:
        return json.load(f), latest.name


def _load_prev_day_prices(user_dir: Path, current_date: str) -> dict[str, float]:
    """Load last_price from the most recent snapshot BEFORE current_date.
    Returns {symbol: last_price} or empty dict if no previous snapshot exists."""
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return {}
    date_dirs = sorted(
        [d for d in holdings_dir.iterdir() if d.is_dir() and d.name < current_date],
        key=lambda d: d.name,
        reverse=True,
    )
    current_dt = date.fromisoformat(current_date)
    for prev in date_dirs:
        prev_dt = date.fromisoformat(prev.name)
        if (current_dt - prev_dt).days > MAX_DAY_MOVER_GAP_DAYS:
            return {}
        hf = prev / "holdings.json"
        if hf.exists():
            with open(hf) as f:
                prev_holdings = json.load(f)
            return {h["symbol"]: h["last_price"] for h in prev_holdings if "last_price" in h}
    return {}


def _load_day_positions(user_dir: Path, snapshot_date: str) -> list[dict]:
    """Load day-trade positions from positions.json for the given snapshot date.
    Returns the 'day' list filtered to entries with non-zero quantity."""
    pos_file = user_dir / "holdings" / snapshot_date / "positions.json"
    if not pos_file.exists():
        return []
    with open(pos_file) as f:
        data = json.load(f)
    return [p for p in data.get("day", []) if p.get("quantity", 0) != 0]


def _load_multiday_prices(
    user_dir: Path,
    current_date: str,
    lookback: int = MULTIDAY_LOOKBACK,
) -> dict[str, dict[str, float]]:
    """Load last_price per symbol for the most recent `lookback` snapshots (including today).
    Returns {date_str: {symbol: last_price}} sorted chronologically.
    """
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return {}
    all_dirs = sorted(
        [d for d in holdings_dir.iterdir() if d.is_dir() and d.name <= current_date],
        key=lambda d: d.name,
        reverse=True,
    )
    selected = []
    for d in all_dirs:
        if len(selected) >= lookback:
            break
        if (d / "holdings.json").exists():
            selected.append(d)
    result = {}
    for d in sorted(selected, key=lambda d: d.name):
        with open(d / "holdings.json") as f:
            h_list = json.load(f)
        result[d.name] = {h["symbol"]: h["last_price"] for h in h_list if "last_price" in h}
    return result


def _load_sector_map() -> dict[str, str]:
    sm_path = PROJECT_ROOT / "sector_map.yaml"
    if not sm_path.exists():
        return {}
    with open(sm_path) as f:
        data = yaml.safe_load(f) or {}
    return {k.upper(): v for k, v in data.items()}


def _load_watchlist_symbols() -> set[str]:
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if not wl_path.exists():
        return set()
    with open(wl_path) as f:
        data = yaml.safe_load(f) or {}
    return {s["symbol"].upper() for s in data.get("stocks", [])}


def _load_company_map() -> dict[str, dict]:
    """Load all companies from watchlist.yaml + portfolio_companies.yaml as {SYMBOL: dict}."""
    company_map: dict[str, dict] = {}
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in company_map:
                company_map[sym] = c
    return company_map


def _sync_watchlist(held_symbols: set[str]) -> list[str]:
    """Remove portfolio-held stocks from watchlist.yaml. Returns list of removed symbols."""
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if not wl_path.exists():
        return []
    with open(wl_path) as f:
        data = yaml.safe_load(f) or {}
    stocks = data.get("stocks", [])
    original_count = len(stocks)
    removed = []
    remaining = []
    for s in stocks:
        sym = s.get("symbol", "").upper()
        if sym in held_symbols:
            removed.append(sym)
        else:
            remaining.append(s)
    if removed:
        data["stocks"] = remaining
        with open(wl_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return removed


# ── DMA fetch (via dma_fetcher skill) ────────────────────────────────────────

def _fetch_dma_batch(
    holdings: list[dict],
    periods: tuple[int, ...] = (40, 99, 100),
) -> dict[str, dict]:
    """
    Fetch DMAs for the given holdings using skills/dma_fetcher.
    Results are cached per day in portfolio/.cache/dma_YYYY-MM-DD.json.
    Returns {symbol: {"dma40": float|None, "dma99": float|None, "source": str}}.
    """
    from skills.dma_fetcher import get_dma  # noqa: PLC0415

    cache_file = CACHE_DIR / f"dma_{date.today().isoformat()}.json"
    cache: dict = {}
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())

    results: dict[str, dict] = {}
    missing = [
        h for h in holdings
        if h["symbol"] not in cache
        or not (cache.get(h["symbol"], {}).get("dma99") or cache.get(h["symbol"], {}).get("dma40"))
    ]

    if missing:
        print(f"  Fetching DMA for {len(missing)} stocks via dma_fetcher...")
    for i, h in enumerate(missing, 1):
        sym = h["symbol"]
        exch = h.get("exchange", "NSE")
        result = get_dma(sym, exch, periods=periods, verbose=False)
        cache[sym] = {
            "dma40": result.get("dma40"),
            "dma99": result.get("dma99"),
            "dma100": result.get("dma100"),
            "source": result.get("source", "none"),
        }
        if i % 10 == 0:
            print(f"    {i}/{len(missing)} done...")

    if missing:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
        resolved = sum(
            1
            for s in missing
            if cache.get(s["symbol"], {}).get("dma99") or cache.get(s["symbol"], {}).get("dma40")
        )
        no_data = [
            h["symbol"]
            for h in missing
            if not (cache.get(h["symbol"], {}).get("dma99") or cache.get(h["symbol"], {}).get("dma40"))
        ]
        print(f"  DMA resolved: {resolved}/{len(missing)}")
        if no_data:
            print(f"  No DMA data for: {', '.join(no_data[:10])}{' …' if len(no_data) > 10 else ''}")

    for h in holdings:
        sym = h["symbol"]
        results[sym] = cache.get(sym, {"dma40": None, "dma99": None, "dma100": None, "source": "none"})
    return results


# ── analytics ────────────────────────────────────────────────────────────────

def _enrich(
    holdings: list[dict],
    prev_prices: dict[str, float] | None = None,
) -> tuple[list[dict], float]:
    """Add pct_of_portfolio and day_change fields. Returns (enriched, total_current_value)."""
    total = sum(h["current_value"] for h in holdings)
    for h in holdings:
        h["pct_of_portfolio"] = round(h["current_value"] / total * 100, 2) if total else 0
        if prev_prices and h["symbol"] in prev_prices:
            prev = prev_prices[h["symbol"]]
            curr = h["last_price"]
            h["day_change_pct"] = (curr - prev) / prev * 100 if prev else 0.0
            h["day_change_inr"] = (curr - prev) * h["quantity"]
        else:
            h["day_change_pct"] = None
            h["day_change_inr"] = None
    return holdings, total


# ── formatting ────────────────────────────────────────────────────────────────

def _inr(amount: float, sign: bool = False) -> str:
    neg = amount < 0
    s = f"₹{abs(amount):,.0f}"
    if sign:
        return f"{'−' if neg else '+'}{s}"
    return f"{'−' if neg else ''}{s}"


def _pct(v: float, sign: bool = True) -> str:
    return f"{'+' if (sign and v >= 0) else ''}{v:.1f}%"


def _append_dma_charts(
    lines: list[str],
    gainers: list[dict],
    losers: list[dict],
    chart_dir: Path | None,
) -> None:
    if not chart_dir:
        return
    chart_dir.mkdir(parents=True, exist_ok=True)
    base_dir = chart_dir.parent

    try:
        from skills.dma_chart import generate_dma_chart  # noqa: PLC0415
    except Exception:
        lines.append("## DMA Charts\n*Chart generation skipped (missing matplotlib).*\n")
        return

    def _render(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.append(title)
        for h in items:
            sym = h["symbol"]
            exch = h.get("exchange", "NSE")
            try:
                path = generate_dma_chart(
                    symbol=sym,
                    exchange=exch,
                    output_dir=chart_dir,
                    lookback_days=CHART_LOOKBACK_DAYS,
                )
            except Exception:
                path = None
            if not path:
                lines.append(f"- **{sym}**: chart unavailable")
                continue
            rel_path = os.path.relpath(path, start=base_dir)
            lines.append(f"**{sym}**\n\n![]({rel_path})\n")

    lines.append("## DMA Charts")
    lines.append("*Price with 40 DMA (yellow) and 99 DMA (black).*\n")
    _render("### Top Gainers Charts", gainers)
    _render("### Top Losers Charts", losers)
    lines.append("")


def _build_multiday_movement_section(
    holdings: list[dict],
    multiday_prices: dict[str, dict[str, float]],
) -> list[str]:
    """Build multi-day price movement table sorted by total % change (fallers first)."""
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

    rows = []
    for h in holdings:
        sym = h["symbol"]
        day_changes = []
        for d_from, d_to in day_pairs:
            p0 = multiday_prices[d_from].get(sym)
            p1 = multiday_prices[d_to].get(sym)
            day_changes.append((p1 - p0) / p0 * 100 if p0 and p1 and p0 > 0 else None)
        p_first = multiday_prices[dates[0]].get(sym)
        p_last  = multiday_prices[dates[-1]].get(sym)
        total_pct = (p_last - p_first) / p_first * 100 if p_first and p_last and p_first > 0 else None
        rows.append({
            "symbol": sym,
            "day_changes": day_changes,
            "total_pct": total_pct,
            "pct_of_portfolio": h["pct_of_portfolio"],
            "last_price": h["last_price"],
        })

    rows_with_total = sorted([r for r in rows if r["total_pct"] is not None], key=lambda x: x["total_pct"])
    rows_no_data    = [r for r in rows if r["total_pct"] is None]

    def _cell(v: float | None) -> str:
        if v is None:
            return "—"
        arrow = "📈" if v > 0.5 else ("📉" if v < -0.5 else "➡️")
        return f"{arrow} {'+' if v >= 0 else ''}{v:.1f}%"

    n = len(dates)
    lines.append(f"## Multi-Day Price Movement ({n} days: {_md(dates[0])} → {_md(dates[-1])})")
    lines.append("*Day-over-day % change per session · sorted by total move (fallers first)*\n")
    lines.append("| Stock | LTP | Alloc% | " + " | ".join(col_headers) + " | Total |")
    lines.append("|---|---|---|" + "|".join(["---"] * len(col_headers)) + "|---|")
    for r in rows_with_total + rows_no_data:
        cells = " | ".join(_cell(c) for c in r["day_changes"])
        lines.append(
            f"| {r['symbol']} | ₹{r['last_price']:.1f} | {r['pct_of_portfolio']:.1f}% | {cells} | {_cell(r['total_pct'])} |"
        )
    lines.append("")

    consistently_falling = [r for r in rows_with_total if all(c is not None and c < 0 for c in r["day_changes"])]
    consistently_rising  = [r for r in rows_with_total if all(c is not None and c > 0 for c in r["day_changes"])]
    _strict_syms = {r["symbol"] for r in consistently_rising + consistently_falling}
    soft_rising = [
        r for r in rows_with_total
        if r["symbol"] not in _strict_syms
        and r["total_pct"] > 1.0
        and all(c is not None and c > -1.0 for c in r["day_changes"])
    ]
    soft_falling = [
        r for r in rows_with_total
        if r["symbol"] not in _strict_syms
        and r["total_pct"] < -1.0
        and all(c is not None and c < 1.0 for c in r["day_changes"])
    ]
    if consistently_falling or consistently_rising or soft_rising or soft_falling:
        lines.append("### Trend Summary")
        if consistently_falling:
            syms = ", ".join(r["symbol"] for r in consistently_falling[:10])
            lines.append(f"📉 **Consistently falling** ({len(consistently_falling)} stocks): {syms}{'  …' if len(consistently_falling) > 10 else ''}")
        if soft_falling:
            syms = ", ".join(r["symbol"] for r in sorted(soft_falling, key=lambda x: x["total_pct"])[:10])
            lines.append(f"🔻 **Soft falling** (trend down >1%, dips <1% bounce, {len(soft_falling)} stocks): {syms}{'  …' if len(soft_falling) > 10 else ''}")
        if consistently_rising:
            syms = ", ".join(r["symbol"] for r in sorted(consistently_rising, key=lambda x: -x["total_pct"])[:10])
            lines.append(f"📈 **Consistently rising** ({len(consistently_rising)} stocks): {syms}{'  …' if len(consistently_rising) > 10 else ''}")
        if soft_rising:
            syms = ", ".join(r["symbol"] for r in sorted(soft_rising, key=lambda x: -x["total_pct"])[:10])
            lines.append(f"🔺 **Soft rising** (trend up >1%, dips <1%, {len(soft_rising)} stocks): {syms}{'  …' if len(soft_rising) > 10 else ''}")
        lines.append("")
    return lines


# ── report builder ────────────────────────────────────────────────────────────

def build_report(
    user_id: str,
    snapshot_date: str,
    holdings: list[dict],
    total_value: float,
    watchlist_symbols: set[str],
    dma_data: dict[str, dict],
    positions: list[dict] | None = None,
    chart_dir: Path | None = None,
    multiday_prices: dict[str, dict[str, float]] | None = None,
) -> str:
    lines: list[str] = []
    invested = sum(h["invested_value"] for h in holdings)
    pnl = total_value - invested
    pnl_pct = pnl / invested * 100 if invested else 0

    lines += [
        f"# Daily Portfolio Health Report",
        f"**{user_id}  ·  {snapshot_date}**\n",
        "## Overview",
        "| | |",
        "|---|---|",
        f"| Invested | {_inr(invested)} |",
        f"| Current Value | {_inr(total_value)} |",
        f"| Total P&L | {_inr(pnl, sign=True)} ({_pct(pnl_pct)}) |",
        f"| Holdings | {len(holdings)} stocks |",
        "",
    ]

    # ── Trades Today ──────────────────────────────────────────────────────────
    if positions:
        held_symbols = {h["symbol"].upper() for h in holdings if h.get("quantity", 0) > 0}
        held_symbols |= {p["symbol"].upper() for p in positions if p["quantity"] > 0}
        buys  = [p for p in positions if p["quantity"] > 0]
        sells = [p for p in positions if p["quantity"] < 0]
        total_trade_pnl = sum(p["pnl"] for p in positions)

        lines.append("## Trades Today")
        lines.append(
            f"*{len(positions)} transactions — {len(buys)} buys, {len(sells)} sells  "
            f"·  Day trade P&L: {_inr(total_trade_pnl, sign=True)}*\n"
        )

        if buys:
            lines.append("**🟢 Bought**")
            lines.append("| Stock | Qty | Avg Price | Day P&L |")
            lines.append("|---|---|---|---|")
            for p in sorted(buys, key=lambda x: -abs(x["quantity"] * x["buy_price"])):
                lines.append(
                    f"| {p['symbol']} | {p['quantity']} "
                    f"| ₹{p['buy_price']:,.2f} "
                    f"| {_inr(p['pnl'], sign=True)} |"
                )
            lines.append("")

        if sells:
            lines.append("**🔴 Sold**")
            lines.append("| Stock | Qty Sold | Sell Price | Day P&L | Status |")
            lines.append("|---|---|---|---|---|")
            for p in sorted(sells, key=lambda x: x["pnl"]):
                status = "FULL EXIT" if p["symbol"].upper() not in held_symbols else "PARTIAL"
                lines.append(
                    f"| {p['symbol']} | {abs(p['quantity'])} "
                    f"| ₹{p['sell_price']:,.2f} "
                    f"| {_inr(p['pnl'], sign=True)} "
                    f"| {status} |"
                )
            lines.append("")

    # ── Position sizing ───────────────────────────────────────────────────────
    oversized = [h for h in holdings if h["pct_of_portfolio"] >= MAX_POSITION_PCT]
    warn_zone = [h for h in holdings if WARN_POSITION_PCT <= h["pct_of_portfolio"] < MAX_POSITION_PCT]

    lines.append("## Position Sizing")
    if oversized:
        lines.append(f"\n⛔ **Over {MAX_POSITION_PCT}% — reduce:**")
        for h in sorted(oversized, key=lambda x: -x["pct_of_portfolio"]):
            excess_inr = h["current_value"] - (total_value * MAX_POSITION_PCT / 100)
            lines.append(
                f"  - **{h['symbol']}**: {h['pct_of_portfolio']:.1f}%  "
                f"({_inr(h['current_value'])})  — reduce by ~{_inr(excess_inr)} to reach 5%"
            )
    if warn_zone:
        lines.append(f"\n🟡 Approaching limit ({WARN_POSITION_PCT}–{MAX_POSITION_PCT}%):")
        for h in sorted(warn_zone, key=lambda x: -x["pct_of_portfolio"]):
            lines.append(f"  - {h['symbol']}: {h['pct_of_portfolio']:.1f}%  ({_inr(h['current_value'])})")
    if not oversized and not warn_zone:
        lines.append("✅ All positions within 5% limit.")
    lines.append("")

    # DMA status moved to evolving files (dma_deployment.md, caution_list.md, deployment_candidates.md)

    # Portfolio concentration moved to evolving file (portfolio_concentration.md)

    # ── Top 10 Daily Movers (today's price vs previous session) ─────────────
    holdings_with_day = [h for h in holdings if h.get("day_change_pct") is not None]
    if holdings_with_day:
        daily_gainers = sorted(holdings_with_day, key=lambda x: -x["day_change_pct"])[:10]
        daily_losers  = sorted(holdings_with_day, key=lambda x:  x["day_change_pct"])[:10]

        lines.append("## Top 10 Daily Movers")
        lines.append("*Based on today's price vs previous session close*\n")

        lines.append("**📈 Top Gainers Today**")
        lines.append("| Stock | LTP | Day% | Day ₹ | Total P&L% | Alloc% |")
        lines.append("|---|---|---|---|---|---|")
        for h in daily_gainers:
            lines.append(
                f"| {h['symbol']} | ₹{h['last_price']:.1f} | {_pct(h['day_change_pct'])} "
                f"| {_inr(h['day_change_inr'], sign=True)} "
                f"| {_pct(h['pnl_pct'])} | {h['pct_of_portfolio']:.1f}% |"
            )
        lines.append("")

        lines.append("**📉 Top Losers Today**")
        lines.append("| Stock | LTP | Day% | Day ₹ | Total P&L% | Alloc% | 99 DMA |")
        lines.append("|---|---|---|---|---|---|---|")
        for h in daily_losers:
            d = (dma_data.get(h["symbol"]) or {}) if dma_data else {}
            dma99 = d.get("dma99")
            dma_str = (f"{'🔴' if h['last_price'] < dma99 else '🟢'} ₹{dma99:.1f}"
                       if dma99 else "—")
            lines.append(
                f"| {h['symbol']} | ₹{h['last_price']:.1f} | {_pct(h['day_change_pct'])} "
                f"| {_inr(h['day_change_inr'], sign=True)} "
                f"| {_pct(h['pnl_pct'])} | {h['pct_of_portfolio']:.1f}% | {dma_str} |"
            )
        lines.append("")

    else:
        lines.append("## Top 10 Daily Movers")
        lines.append("*No previous-day snapshot available — run daily to build history.*\n")

    # ── Multi-day price movement ───────────────────────────────────────────────
    if multiday_prices:
        lines += _build_multiday_movement_section(holdings, multiday_prices)

        # Charts for top/bottom stocks by multi-day total % move
        _dates = sorted(multiday_prices.keys())
        if len(_dates) >= 2:
            _moves = []
            for h in holdings:
                p0 = multiday_prices[_dates[0]].get(h["symbol"])
                p1 = multiday_prices[_dates[-1]].get(h["symbol"])
                if p0 and p1 and p0 > 0:
                    _moves.append(((p1 - p0) / p0 * 100, h))
            _moves.sort(key=lambda x: x[0])
            md_fallers = [h for _, h in _moves[:TOP_CHART_COUNT]]
            md_risers  = [h for _, h in _moves[-TOP_CHART_COUNT:][::-1]]
            _append_dma_charts(lines, md_risers, md_fallers, chart_dir)

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(user_id: str | None = None, fetch_dma: bool = True, lookback: int = MULTIDAY_LOOKBACK) -> Path:
    user_dir = _find_user_dir(user_id)
    holdings_raw, snapshot_date = _load_latest_holdings(user_dir)
    prev_prices = _load_prev_day_prices(user_dir, snapshot_date)
    sector_map = _load_sector_map()
    watchlist_symbols = _load_watchlist_symbols()
    company_map = _load_company_map()
    day_positions = _load_day_positions(user_dir, snapshot_date)
    multiday_prices = _load_multiday_prices(user_dir, snapshot_date, lookback=lookback)
    holdings, total_value = _enrich(holdings_raw, prev_prices)

    print(f"\n{'='*60}")
    print(f"  PORTFOLIO DAILY REPORT  —  {user_dir.name}  ·  {snapshot_date}")
    print(f"{'='*60}")
    print(f"  {len(holdings)} holdings  |  Invested: {_inr(sum(h['invested_value'] for h in holdings))}")

    # DMA — fetch for all holdings + watchlist-only stocks (daily yfinance refresh)
    dma_data: dict = {}
    if fetch_dma:
        # Fetch DMA for holdings
        print(f"\n  Fetching DMA for {len(holdings)} holdings...")
        dma_data = _fetch_dma_batch(holdings)

        # Also fetch DMA for watchlist stocks not in holdings (for watchlist_not_held section)
        held_syms = {h["symbol"] for h in holdings}
        wl_only = [
            {"symbol": sym, "exchange": company_map.get(sym, {}).get("exchange", "NSE")}
            for sym in watchlist_symbols if sym not in held_syms
        ]
        if wl_only:
            print(f"  Fetching DMA for {len(wl_only)} watchlist-only stocks...")
            wl_dma = _fetch_dma_batch(wl_only)
            dma_data.update(wl_dma)

    chart_dir = user_dir / "holdings" / snapshot_date / "charts"
    report_md = build_report(
        user_id=user_dir.name,
        snapshot_date=snapshot_date,
        holdings=holdings,
        total_value=total_value,
        watchlist_symbols=watchlist_symbols,
        dma_data=dma_data,
        positions=day_positions if day_positions else None,
        chart_dir=chart_dir,
        multiday_prices=multiday_prices if len(multiday_prices) >= 2 else None,
    )

    out_file = user_dir / "holdings" / snapshot_date / "daily_report.md"
    out_file.write_text(report_md, encoding="utf-8")
    print(f"\n✓ Report saved → {out_file}")

    # Write daily evolving files (DMA deployment, sector, candidates, caution, watchlist, SME)
    if fetch_dma and holdings:
        from workflows.shared_sections import write_daily_evolving_files  # noqa: PLC0415

        print(f"\n  Writing daily evolving files → {user_dir}/")
        written = write_daily_evolving_files(
            user_dir=user_dir,
            holdings=holdings,
            dma_data=dma_data,
            company_map=company_map,
            watchlist_symbols=watchlist_symbols,
        )
        for p in written:
            print(f"    ✓ {p.name}")

    # Watchlist sync: remove portfolio-held stocks from watchlist.yaml
    held_syms = {h["symbol"] for h in holdings}
    removed = _sync_watchlist(held_syms)
    if removed:
        print(f"\n  🔄 Watchlist sync: removed {len(removed)} held stocks → {', '.join(removed)}")

    print(f"\n✓ Done\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily portfolio health report")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one user exists)")
    parser.add_argument("--no-dma", action="store_true", help="Skip 200 DMA fetch (faster, no Kite call)")
    parser.add_argument("--lookback", type=int, default=MULTIDAY_LOOKBACK, help=f"Days of history for multi-day movement section (default: {MULTIDAY_LOOKBACK})")
    args = parser.parse_args()

    run(user_id=args.user, fetch_dma=not args.no_dma, lookback=args.lookback)
