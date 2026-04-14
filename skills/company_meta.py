"""
Company Meta
=============
Stores and retrieves the authoritative company name and sector scraped from Screener.in.

Each company gets:  data/companies/{SYMBOL}/meta.json
  {"symbol": "UNIECOM", "name": "Unicommerce E Solutions Ltd",
   "sector": "E-commerce SaaS", "screener_url": "..."}

Used by all skills/workflows to ensure consistent company names across reports.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def _meta_path(symbol: str) -> Path:
    from llm.client import get_config
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    return data_root / symbol.upper() / "meta.json"


def _read_meta(symbol: str) -> dict:
    """Read meta.json for a symbol, returning {} on any error."""
    p = _meta_path(symbol)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_company_name(symbol: str, fallback: str | None = None) -> str:
    """
    Return the authoritative company name for this symbol.
    Priority: meta.json > fallback > symbol itself.
    """
    name = (_read_meta(symbol).get("name") or "").strip()
    if name and len(name) > 2:
        return name
    return fallback or symbol


def get_company_sector(symbol: str, fallback: str | None = None) -> str | None:
    """
    Return the authoritative sector for this symbol from meta.json.
    Returns fallback (or None) if not stored.
    """
    sector = (_read_meta(symbol).get("sector") or "").strip()
    return sector if sector else fallback


def store_company_meta(
    symbol: str,
    name: str = "",
    screener_url: str = "",
    sector: str = "",
) -> None:
    """Persist company metadata to meta.json (merge with existing)."""
    p = _meta_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_meta(symbol)
    existing["symbol"] = symbol.upper()
    if name and len(name) > 2:
        existing["name"] = name
    if screener_url:
        existing["screener_url"] = screener_url
    if sector and len(sector) > 1:
        existing["sector"] = sector
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def get_latest_price(symbol: str) -> dict:
    """
    Get the most recent stock price for a symbol.

    Priority:
      1. Latest holdings.json from portfolio snapshots (most recent date)
      2. yfinance live quote (fallback for watchlist-only / non-held stocks)

    Returns:
        {"price": float, "source": str, "date": str} or empty dict if unavailable.
    """
    sym = symbol.upper()

    # ── 1. Portfolio holdings (most recent snapshot) ──────────────────────────
    portfolio_dir = PROJECT_ROOT / "portfolio"
    if portfolio_dir.exists():
        user_dirs = sorted(
            [d for d in portfolio_dir.iterdir() if d.is_dir() and d.name != ".cache"],
            key=lambda d: d.name,
        )
        for user_dir in reversed(user_dirs):
            holdings_root = user_dir / "holdings"
            if not holdings_root.exists():
                continue
            date_dirs = sorted(
                [d for d in holdings_root.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            for dd in date_dirs:
                hf = dd / "holdings.json"
                if not hf.exists():
                    continue
                try:
                    holdings = json.loads(hf.read_text(encoding="utf-8"))
                    for h in holdings:
                        if h.get("symbol", "").upper() == sym and "last_price" in h:
                            return {
                                "price": float(h["last_price"]),
                                "source": f"portfolio ({dd.name})",
                                "date": dd.name,
                            }
                except Exception:
                    continue

    # ── 2. yfinance fallback ─────────────────────────────────────────────────
    try:
        import yfinance as yf

        clean = sym.replace("-SM", "").replace("-BE", "")
        for suffix in [".NS", ".BO"]:
            try:
                ticker = yf.Ticker(f"{clean}{suffix}")
                info = ticker.fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                if price and price > 0:
                    return {
                        "price": round(float(price), 2),
                        "source": f"yfinance ({clean}{suffix})",
                        "date": "",
                    }
            except Exception:
                continue
    except ImportError:
        pass

    return {}


def scrape_company_info(screener_url: str) -> dict:
    """
    Fetch the Screener page via Chrome CDP and extract:
      - name: from <h1>
      - sector: from sector link (href contains 'Sector+%3D' or 'Industry+%3D')

    Returns a dict with keys 'name' and/or 'sector' (each only present if found).
    """
    result: dict = {}
    try:
        from skills.cdp_helper import fetch_page_html as _cdp_fetch
        from bs4 import BeautifulSoup
        html = _cdp_fetch(screener_url.rstrip("/"))
        if not html:
            return result
        soup = BeautifulSoup(html, "lxml")

        # ── Company name ─────────────────────────────────────────────────────
        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)
            if name and len(name) > 3:
                result["name"] = name

        # ── Sector: look for anchor whose href has Sector or Industry query ──
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "Sector" in href or "Industry" in href:
                sector = a.get_text(strip=True)
                if sector and len(sector) > 2:
                    result["sector"] = sector
                    break

        # ── Fallback: look for "Industry" label in company info section ──────
        if "sector" not in result:
            for tag in soup.find_all(["span", "td", "th"]):
                if "Industry" in tag.get_text():
                    nxt = tag.find_next_sibling()
                    if nxt:
                        sector = nxt.get_text(strip=True)
                        if sector and len(sector) > 2 and len(sector) < 80:
                            result["sector"] = sector
                            break
    except Exception:
        pass
    return result


def scrape_company_name(screener_url: str) -> str | None:
    """Convenience wrapper — returns just the name (or None)."""
    return scrape_company_info(screener_url).get("name")
