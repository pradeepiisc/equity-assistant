"""
DMA Fetcher
===========
Retrieves 40 DMA (short-term) and 99 DMA (long-term) for a given stock using multiple strategies,
tried in order until one succeeds:

  1. yfinance  (free, NSE: SYMBOL.NS / BSE: SYMBOL.BO)
  2. Screener.in price history API (HTTP, no JS)
  3. Tickertape technicals API (HTTP)

Returns:
    {
        "symbol": str,
        "exchange": str,
        "dma40":  float | None,
        "dma99": float | None,
        "source": str,          # which strategy succeeded
        "closes": int,          # number of close prices used
    }

Usage (CLI):
    python -m skills.dma_fetcher SYRMA
    python -m skills.dma_fetcher SYRMA NSE
    python -m skills.dma_fetcher BEWLTD BSE
    python -m skills.dma_fetcher SONAMAC BSE --periods 40 99
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

_EMPTY = {"dma40": None, "dma99": None, "source": "none", "closes": 0}


# ── helpers ────────────────────────────────────────────────────────────────────

def _compute_dmas(closes: list[float], periods: Sequence[int]) -> dict[str, float | None]:
    result = {}
    for p in periods:
        key = f"dma{p}"
        if len(closes) >= p:
            result[key] = round(sum(closes[-p:]) / p, 2)
        else:
            result[key] = None
    return result


def _yf_ticker(symbol: str, exchange: str) -> str:
    """Map portfolio symbol + exchange to a yfinance ticker string."""
    sym = symbol.upper().replace(" ", "").replace("-SM", "").replace("-BE", "")
    exch = exchange.upper()
    suffix = ".BO" if exch == "BSE" else ".NS"
    return sym + suffix


# ── Strategy 1: local portfolio snapshots ─────────────────────────────────────

def _from_local(
    symbol: str,
    periods: Sequence[int],
    user_id: str | None = None,
) -> dict | None:
    """
    Scans all dated portfolio snapshots and builds a price series from
    holdings.json 'last_price'. Returns DMA dict if enough data.
    """
    if not PORTFOLIO_DIR.exists():
        return None

    user_dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if user_id:
        user_dirs = [d for d in user_dirs if d.name.upper() == user_id.upper()]
    if not user_dirs:
        return None
    user_dir = user_dirs[0]

    closes: list[tuple[str, float]] = []
    _holdings_root = user_dir / "holdings"
    _scan_dirs = sorted(d for d in _holdings_root.iterdir() if d.is_dir()) if _holdings_root.exists() else []
    for date_dir in _scan_dirs:
        hf = date_dir / "holdings.json"
        if not hf.exists():
            continue
        try:
            holdings = json.loads(hf.read_text())
            for h in holdings:
                if h.get("symbol", "").upper() == symbol.upper():
                    closes.append((date_dir.name, float(h["last_price"])))
                    break
        except Exception:
            continue

    if not closes:
        return None

    price_series = [p for _, p in sorted(closes)]
    max_period = max(periods)
    if len(price_series) < max(10, max_period // 10):
        return None  # not enough local history to be meaningful

    dmas = _compute_dmas(price_series, periods)
    dmas["source"] = f"local ({len(price_series)} snapshots)"
    dmas["closes"] = len(price_series)
    return dmas


# ── Strategy 2: yfinance ───────────────────────────────────────────────────────

def _from_yfinance(
    symbol: str,
    exchange: str,
    periods: Sequence[int],
) -> dict | None:
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return None

    import io, sys  # noqa: PLC0415
    from datetime import date as _date, timedelta  # noqa: PLC0415

    def _quiet_history(ticker_str: str, start: str):
        """Call yf.Ticker.history() using start= date (more reliable than period=Nd)."""
        captured = io.StringIO()
        old_err, old_out = sys.stderr, sys.stdout
        sys.stderr = sys.stdout = captured
        try:
            hist = yf.Ticker(ticker_str).history(start=start, auto_adjust=False)
        finally:
            sys.stderr, sys.stdout = old_err, old_out
        return hist

    sym_orig = symbol.upper().replace(" ", "")
    sym_clean = sym_orig.replace("-SM", "").replace("-BE", "")
    exch = exchange.upper()
    primary, alt_exch = (".NS", ".BO") if exch != "BSE" else (".BO", ".NS")
    candidates = list(dict.fromkeys(
        s + sfx
        for s in ([sym_orig] if sym_orig != sym_clean else []) + [sym_clean]
        for sfx in [primary, alt_exch]
    ))

    max_p = max(periods)
    lookback_days = int(max_p * (365 / 252)) + 60
    start_date = (_date.today() - timedelta(days=lookback_days)).isoformat()
    try:
        hist = None
        ticker_str = candidates[0]
        for candidate in candidates:
            h = _quiet_history(candidate, start_date)
            if not h.empty and (hist is None or len(h) > len(hist)):
                hist = h
                ticker_str = candidate
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna().tolist()
        if len(closes) < 20:
            return None
        dmas = _compute_dmas(closes, periods)
        dmas["source"] = f"yfinance ({ticker_str})"
        dmas["closes"] = len(closes)
        dmas["last_price"] = round(closes[-1], 2)
        return dmas
    except Exception:
        return None


# ── Strategy 3: Screener.in ───────────────────────────────────────────────────

def _screener_company_id(symbol: str) -> str | None:
    """Fetch the Screener.in URL slug for a symbol."""
    try:
        url = f"https://www.screener.in/search/?q={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        link = soup.select_one("ul.list-links a")
        if link:
            href = link.get("href", "")
            # /company/SYRMA/ → SYRMA
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) >= 2:
                return parts[1]  # the slug after "company"
    except Exception:
        pass
    return None


def _from_screener(
    symbol: str,
    periods: Sequence[int],
) -> dict | None:
    """
    Fetches Screener.in price history chart API and computes DMA.
    URL: https://www.screener.in/api/company/{slug}/chart/?q=Price&days={days}
    """
    try:
        slug = _screener_company_id(symbol)
        if not slug:
            return None

        max_p = max(periods)
        days = int(max_p * 1.5 * (365 / 252)) + 60  # calendar days buffer

        url = (
            f"https://www.screener.in/api/company/{slug}/chart/"
            f"?q=Price&days={days}&consolidated=false"
        )
        r = requests.get(url, headers={**HEADERS, "Referer": "https://www.screener.in/"}, timeout=15)
        data = r.json()

        # Response: {"datasets": [{"metric": "Price", "values": [[ts, price], ...]}]}
        prices_raw = None
        for ds in data.get("datasets", []):
            if ds.get("metric", "").lower() == "price":
                prices_raw = ds.get("values", [])
                break

        if not prices_raw:
            return None

        closes = [float(v[1]) for v in prices_raw if v[1] is not None]
        if len(closes) < 20:
            return None

        dmas = _compute_dmas(closes, periods)
        dmas["source"] = f"screener.in ({slug})"
        dmas["closes"] = len(closes)
        return dmas
    except Exception:
        return None


# ── Strategy 4: Tickertape (HTTP, no JS needed for technicals API) ─────────────

def _from_tickertape(
    symbol: str,
    exchange: str,
    periods: Sequence[int],
) -> dict | None:
    """
    Tickertape technicals endpoint returns JSON with moving averages.
    URL: https://api.tickertape.in/stocks/charts/inter?sid={SID}&span=3y&type=price
    Requires SID lookup first via search.
    """
    try:
        # Step 1: find SID
        search_url = f"https://api.tickertape.in/search?text={symbol}&type=stock"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        results = r.json().get("data", {}).get("stocks", [])
        sid = None
        for item in results:
            if item.get("ticker", "").upper() == symbol.upper():
                sid = item.get("slug") or item.get("sid")
                break
        if not sid and results:
            sid = results[0].get("slug") or results[0].get("sid")
        if not sid:
            return None

        # Step 2: fetch price history
        hist_url = f"https://api.tickertape.in/stocks/charts/inter?sid={sid}&span=3y&type=price"
        r2 = requests.get(hist_url, headers=HEADERS, timeout=15)
        points = r2.json().get("data", {}).get("points", [])
        closes = [float(p["cp"]) for p in points if p.get("cp") is not None]
        if len(closes) < 20:
            return None

        dmas = _compute_dmas(closes, periods)
        dmas["source"] = f"tickertape ({sid})"
        dmas["closes"] = len(closes)
        return dmas
    except Exception:
        return None


# ── public API ────────────────────────────────────────────────────────────────

def get_dma(
    symbol: str,
    exchange: str = "NSE",
    periods: Sequence[int] = (40, 99),
    cdp_url: str = "http://127.0.0.1:9222",
    verbose: bool = False,
) -> dict:
    """
    Return DMA dict for the given stock, trying strategies in order.

    Args:
        symbol:   Kite/portfolio trading symbol (e.g. "SYRMA", "BEWLTD-SM")
        exchange: "NSE" or "BSE"
        periods:  DMA periods to compute, default (40, 99)
        cdp_url:  Chrome DevTools URL for CDP fallback
        verbose:  print which strategy was tried

    Returns:
        {"symbol": .., "exchange": .., "dma40": .., "dma99": .., "source": .., "closes": ..}
    """
    strategies = [
        # _from_local removed: daily snapshot last_price is intraday price (not official close),
        # would need 200+ daily-close snapshots to be meaningful for 200 DMA.
        ("yfinance",    lambda: _from_yfinance(symbol, exchange, periods)),
        ("screener",    lambda: _from_screener(symbol, periods)),
        ("tickertape",  lambda: _from_tickertape(symbol, exchange, periods)),
    ]

    for name, fn in strategies:
        if verbose:
            print(f"  [{symbol}] trying {name}...", end=" ", flush=True)
        try:
            result = fn()
        except Exception as exc:
            result = None
            if verbose:
                print(f"error ({exc})")
            continue
        if result and (result.get("dma40") or result.get("dma99")):
            if verbose:
                print(f"✓  dma40={result.get('dma40')}  dma99={result.get('dma99')}")
            return {"symbol": symbol, "exchange": exchange, **result}
        if verbose:
            print("no data")

    return {"symbol": symbol, "exchange": exchange, **_EMPTY}


def get_dma_batch(
    holdings: list[dict],
    periods: Sequence[int] = (40, 99),
    cdp_url: str = "http://127.0.0.1:9222",
    verbose: bool = True,
) -> dict[str, dict]:
    """
    Fetch DMAs for a list of holding dicts (each needs 'symbol' and 'exchange').
    Returns {symbol: dma_dict}.
    """
    results: dict[str, dict] = {}
    for h in holdings:
        sym = h["symbol"]
        exch = h.get("exchange", "NSE")
        results[sym] = get_dma(sym, exch, periods=periods, cdp_url=cdp_url, verbose=verbose)
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch 40/99 DMA for a stock")
    parser.add_argument("symbol", help="Trading symbol e.g. SYRMA")
    parser.add_argument("exchange", nargs="?", default="NSE", help="NSE or BSE (default: NSE)")
    parser.add_argument(
        "--periods", nargs="+", type=int, default=[40, 99], metavar="N", help="DMA periods"
    )
    parser.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP URL")
    args = parser.parse_args()

    result = get_dma(args.symbol, args.exchange, periods=args.periods, cdp_url=args.cdp, verbose=True)
    print(f"\nResult for {result['symbol']} ({result['exchange']}):")
    print(f"  40 DMA : {result.get('dma40')}")
    print(f"  99 DMA : {result.get('dma99')}")
    print(f"  Source  : {result.get('source')}")
    print(f"  Closes  : {result.get('closes')}")
