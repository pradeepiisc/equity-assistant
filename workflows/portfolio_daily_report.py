"""
Workflow: Daily Portfolio Health Report
========================================
Reads the latest Kite portfolio snapshot and produces a daily health report:

  1. Overview — invested, current value, total P&L
  2. Trades Today — buys and sells from positions.json
  3. Portfolio Concentration — all stocks sorted by value with 50/70/90% milestones
  4. Top 10 Daily Movers — day % change vs previous session close
  5. Watchlist stocks currently held

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
        
    holdings_file = holdings_dir / "holdings.json"
    if not holdings_file.exists():
        raise FileNotFoundError(f"holdings.json missing in {holdings_dir}")

    # Get date from running_holdings.csv
    running_csv = holdings_dir / "running_holdings.csv"
    latest_date = "unknown"
    if running_csv.exists():
        import pandas as pd
        df = pd.read_csv(running_csv, nrows=0) # just read header
        date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
        if date_cols:
            latest_date = date_cols[-1]
            
    with open(holdings_file) as f:
        data = json.load(f)
        import re
        for h in data:
            if "symbol" in h:
                h["symbol"] = re.sub(r'-[A-Z]{2}$', '', h["symbol"], flags=re.IGNORECASE)
        return [h for h in data if h.get("quantity", 0) > 0], latest_date


def _load_prev_day_prices(user_dir: Path, current_date: str) -> dict[str, float]:
    """Load last_price from the most recent snapshot BEFORE current_date.
    Returns {symbol: last_price} or empty dict if no previous snapshot exists."""
    holdings_dir = user_dir / "holdings"
    running_csv = holdings_dir / "running_holdings.csv"
    if not running_csv.exists():
        return {}
        
    import pandas as pd
    df = pd.read_csv(running_csv)
    date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
    
    if current_date == "unknown":
        return {}
        
    current_dt = date.fromisoformat(current_date)
    prev_date = None
    
    for c in reversed(date_cols):
        if c < current_date:
            try:
                dt = date.fromisoformat(c)
                if (current_dt - dt).days <= MAX_DAY_MOVER_GAP_DAYS:
                    prev_date = c
                break
            except ValueError:
                continue
                
    if not prev_date:
        return {}
        
    return {row['symbol']: row[prev_date] for _, row in df.iterrows() if pd.notnull(row.get(prev_date)) and row[prev_date] > 0}


def _load_day_positions(user_dir: Path, snapshot_date: str) -> list[dict]:
    """Load day-trade positions from positions.json.
    Returns the 'day' list filtered to entries with non-zero quantity."""
    pos_file = user_dir / "holdings" / "positions.json"
    if not pos_file.exists():
        return []
    with open(pos_file) as f:
        data = json.load(f)
    
    import re
    day_positions = data.get("day", [])
    for p in day_positions:
        if "symbol" in p:
            p["symbol"] = re.sub(r'-[A-Z]{2}$', '', p["symbol"], flags=re.IGNORECASE)
            
    return [p for p in day_positions if p.get("quantity", 0) != 0]


def _load_multiday_prices(
    user_dir: Path,
    current_date: str,
    lookback: int = MULTIDAY_LOOKBACK,
) -> dict[str, dict[str, float]]:
    """Load last_price per symbol for the most recent `lookback` snapshots (including today).
    Returns {date_str: {symbol: last_price}} sorted chronologically.
    """
    holdings_dir = user_dir / "holdings"
    running_csv = holdings_dir / "running_holdings.csv"
    if not running_csv.exists() or current_date == "unknown":
        return {}
        
    import pandas as pd
    df = pd.read_csv(running_csv)
    date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
    
    valid_dates = [c for c in date_cols if c <= current_date]
    selected_dates = valid_dates[-lookback:]
    
    result = {}
    for d in selected_dates:
        result[d] = {row['symbol']: row[d] for _, row in df.iterrows() if pd.notnull(row.get(d)) and row[d] > 0}
    return result


def _detect_mth_stocks(
    user_dir: Path,
    current_date: str,
    holdings: list[dict],
) -> list[dict]:
    """
    Detect MTH (Mid-Time High) stocks - stocks hitting new highs based on historical data.
    Returns list of stocks with new MTH status, enriched with allocation and P&L info.
    """
    holdings_dir = user_dir / "holdings"
    running_csv = holdings_dir / "running_holdings.csv"
    if not running_csv.exists() or current_date == "unknown":
        return []
        
    import pandas as pd
    df = pd.read_csv(running_csv)
    date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
    
    # Get all historical dates up to current date (excluding today for comparison)
    historical_dates = [c for c in date_cols if c < current_date]
    if not historical_dates:
        return []
    
    # Create symbol -> current price mapping
    current_prices = {h["symbol"]: h["last_price"] for h in holdings}
    
    mth_stocks = []
    
    for _, row in df.iterrows():
        symbol = row['symbol']
        if symbol not in current_prices:
            continue
            
        current_price = current_prices[symbol]
        if current_price <= 0:
            continue
        
        # Get historical prices for this symbol
        historical_prices = []
        for date in historical_dates:
            price = row.get(date)
            if pd.notnull(price) and price > 0:
                historical_prices.append(price)
        
        if not historical_prices:
            continue
        
        # Check if current price is the highest ever
        historical_high = max(historical_prices)
        if current_price > historical_high:
            # Find the holding record to get allocation info
            holding = next((h for h in holdings if h["symbol"] == symbol), None)
            if holding:
                mth_stocks.append({
                    **holding,
                    "historical_high": historical_high,
                    "mth_pct_above": ((current_price - historical_high) / historical_high * 100) if historical_high > 0 else 0,
                    "days_of_history": len(historical_prices),
                })
    
    # Sort by percentage above historical high (descending)
    mth_stocks.sort(key=lambda x: x["mth_pct_above"], reverse=True)
    return mth_stocks


def _detect_mtl_stocks(
    user_dir: Path,
    current_date: str,
    holdings: list[dict],
) -> list[dict]:
    """
    Detect MTL (Mid-Time Low) stocks - stocks hitting new lows based on historical data.
    Returns list of stocks with new MTL status, enriched with allocation and P&L info.
    """
    holdings_dir = user_dir / "holdings"
    running_csv = holdings_dir / "running_holdings.csv"
    if not running_csv.exists() or current_date == "unknown":
        return []
        
    import pandas as pd
    df = pd.read_csv(running_csv)
    date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
    
    # Get all historical dates up to current date (excluding today for comparison)
    historical_dates = [c for c in date_cols if c < current_date]
    if not historical_dates:
        return []
    
    # Create symbol -> current price mapping
    current_prices = {h["symbol"]: h["last_price"] for h in holdings}
    
    mtl_stocks = []
    
    for _, row in df.iterrows():
        symbol = row['symbol']
        if symbol not in current_prices:
            continue
            
        current_price = current_prices[symbol]
        if current_price <= 0:
            continue
        
        # Get historical prices for this symbol
        historical_prices = []
        for date in historical_dates:
            price = row.get(date)
            if pd.notnull(price) and price > 0:
                historical_prices.append(price)
        
        if not historical_prices:
            continue
        
        # Check if current price is the lowest ever
        historical_low = min(historical_prices)
        if current_price < historical_low:
            # Find the holding record to get allocation info
            holding = next((h for h in holdings if h["symbol"] == symbol), None)
            if holding:
                mtl_stocks.append({
                    **holding,
                    "historical_low": historical_low,
                    "mtl_pct_below": ((historical_low - current_price) / historical_low * 100) if historical_low > 0 else 0,
                    "days_of_history": len(historical_prices),
                })
    
    # Sort by percentage below historical low (descending - worst first)
    mtl_stocks.sort(key=lambda x: x["mtl_pct_below"], reverse=True)
    return mtl_stocks


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
    mth_stocks: list[dict] | None = None,
    mtl_stocks: list[dict] | None = None,
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


    # DMA status moved to evolving files (dma_deployment.md, caution_list.md, deployment_candidates.md)

    # ── Portfolio Concentration ─────────────────────────────────────────────────
    from workflows.shared_sections import render_portfolio_concentration
    lines.extend(render_portfolio_concentration(holdings, watchlist_symbols))

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

    # ── MTH (Mid-Time High) Stocks ───────────────────────────────────────────────
    if mth_stocks:
        lines.append("## 🏆 MTH (Mid-Time High) Stocks")
        lines.append(f"*{len(mth_stocks)} stocks hitting new all-time highs based on historical data*\n")
        
        lines.append("| Stock | LTP | Historical High | % Above High | Total P&L% | Alloc% | Days of History |")
        lines.append("|---|---|---|---|---|---|---|")
        for stock in mth_stocks:
            lines.append(
                f"| {stock['symbol']} | ₹{stock['last_price']:.1f} "
                f"| ₹{stock['historical_high']:.1f} "
                f"| {_pct(stock['mth_pct_above'])} "
                f"| {_pct(stock['pnl_pct'])} "
                f"| {stock['pct_of_portfolio']:.1f}% "
                f"| {stock['days_of_history']} |"
            )
        lines.append("")
    else:
        lines.append("## 🏆 MTH (Mid-Time High) Stocks")
        lines.append("*No stocks hitting new all-time highs today.*\n")

    # ── MTL (Mid-Time Low) Stocks ────────────────────────────────────────────────
    if mtl_stocks:
        lines.append("## 📉 MTL (Mid-Time Low) Stocks")
        lines.append(f"*{len(mtl_stocks)} stocks hitting new all-time lows based on historical data*\n")
        
        lines.append("| Stock | LTP | Historical Low | % Below Low | Total P&L% | Alloc% | Days of History |")
        lines.append("|---|---|---|---|---|---|---|")
        for stock in mtl_stocks:
            lines.append(
                f"| {stock['symbol']} | ₹{stock['last_price']:.1f} "
                f"| ₹{stock['historical_low']:.1f} "
                f"| {_pct(-stock['mtl_pct_below'])} "
                f"| {_pct(stock['pnl_pct'])} "
                f"| {stock['pct_of_portfolio']:.1f}% "
                f"| {stock['days_of_history']} |"
            )
        lines.append("")
    else:
        lines.append("## 📉 MTL (Mid-Time Low) Stocks")
        lines.append("*No stocks hitting new all-time lows today.*\n")

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
    
    # Detect MTH and MTL stocks
    mth_stocks = _detect_mth_stocks(user_dir, snapshot_date, holdings)
    mtl_stocks = _detect_mtl_stocks(user_dir, snapshot_date, holdings)
    if mth_stocks:
        print(f"  🏆 {len(mth_stocks)} MTH stocks detected")
    if mtl_stocks:
        print(f"  📉 {len(mtl_stocks)} MTL stocks detected")

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

    chart_dir = user_dir / "charts"
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
        mth_stocks=mth_stocks,
        mtl_stocks=mtl_stocks,
    )

    out_file = user_dir / "daily_report.md"
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
