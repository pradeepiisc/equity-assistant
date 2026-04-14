"""
Workflow: Daily News Digest
============================
Fetches 3 recent headlines per stock for:
  • Top N holdings by current value  (default 20)
  • All stocks in watchlist.yaml

Aggregates into a single markdown digest saved to:
  portfolio/{user}/{today}/news_digest.md

Does NOT scrape full article content — headlines + source + date only.
Fast: ~0.3s per stock, ~20 stocks ≈ 6-8s total.

Usage:
    python -m workflows.news_digest
    python -m workflows.news_digest --user ZV3899 --top 15
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FETCH_DELAY = 0.3   # polite delay between RSS fetches
HEADLINES_PER_STOCK = 3


# ── data loaders ──────────────────────────────────────────────────────────────

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
    raise ValueError(f"Multiple users: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_top_holdings(user_dir: Path, top_n: int) -> list[dict]:
    """Return top N holdings by current_value from the latest snapshot."""
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return []

    date_dirs = sorted((d for d in holdings_dir.iterdir() if d.is_dir()), reverse=True)
    for d in date_dirs:
        hf = d / "holdings.json"
        if hf.exists():
            holdings = json.loads(hf.read_text())
            # Compute current_value if not present
            for h in holdings:
                if "current_value" not in h:
                    h["current_value"] = h.get("last_price", 0) * h.get("quantity", 0)
            return sorted(holdings, key=lambda x: -x["current_value"])[:top_n]
    return []


def _latest_snapshot_date(user_dir: Path) -> str:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings directory found under: {user_dir}")

    date_dirs = sorted((d for d in holdings_dir.iterdir() if d.is_dir()), reverse=True)
    if not date_dirs:
        raise FileNotFoundError(f"No dated holdings snapshots found under: {holdings_dir}")

    return date_dirs[0].name


def _load_watchlist() -> list[dict]:
    path = PROJECT_ROOT / "watchlist.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


# ── news fetcher ───────────────────────────────────────────────────────────────

def _pubdate_to_snapshot_date(pub_str: str) -> str | None:
    """Convert RSS pubDate to Asia/Kolkata YYYY-MM-DD for snapshot-date matching."""
    try:
        dt = parsedate_to_datetime(pub_str)
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.date().isoformat()
        return dt.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    except Exception:
        return None


def _fetch_headlines(
    symbol: str,
    company_name: str,
    limit: int = HEADLINES_PER_STOCK,
    snapshot_date: str | None = None,
) -> list[dict]:
    if symbol.upper() == "J&KBANK":
        return []
    """Fetch headlines from Google News RSS. Returns list of {title, source, published, link}."""
    query = f"{company_name.replace(' ', '+')}+{symbol}+NSE"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(url, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        articles = []
        for item in root.findall(".//item"):
            title = re.sub(r"<[^>]+>", "", item.findtext("title", "")).strip()
            source_tag = item.find("source")
            source = source_tag.text.strip() if source_tag is not None else ""
            pub_raw = item.findtext("pubDate", "").strip()
            if snapshot_date:
                pub_date = _pubdate_to_snapshot_date(pub_raw)
                if pub_date != snapshot_date:
                    continue

            pub = pub_raw[:16]
            link = item.findtext("link", "")
            articles.append({"title": title, "source": source, "published": pub, "link": link})

            if len(articles) >= limit:
                break
        return articles
    except Exception:
        return []


def _age_label(pub_str: str) -> str:
    """Return human-friendly age from RFC 2822 date string."""
    try:
        dt = datetime.strptime(pub_str[:25], "%a, %d %b %Y %H:%M:%S")
        delta = datetime.utcnow() - dt
        h = int(delta.total_seconds() / 3600)
        if h < 1:
            return "< 1h ago"
        if h < 24:
            return f"{h}h ago"
        return f"{delta.days}d ago"
    except Exception:
        return pub_str[:16] if pub_str else ""


# ── digest builder ─────────────────────────────────────────────────────────────

def build_digest(
    results: list[dict],   # [{symbol, name, headlines: [{title, source, published, link}]}]
    snapshot_date: str,
    user_id: str,
) -> str:
    lines = [
        "# Daily News Digest",
        f"**{user_id}  ·  {snapshot_date}**\n",
        f"*{sum(len(r['headlines']) for r in results)} headlines across {len(results)} stocks*\n",
    ]

    no_news = []
    for r in results:
        if not r["headlines"]:
            no_news.append(r["symbol"])
            continue
        lines.append(f"## {r['symbol']}  —  {r['name']}")
        for h in r["headlines"]:
            age = _age_label(h["published"])
            src = h["source"]
            title = h["title"]
            link = h["link"]
            lines.append(f"- [{title}]({link})  *{src} · {age}*")
        lines.append("")

    if no_news:
        lines.append(f"*No recent news found for: {', '.join(no_news)}*\n")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(
    user_id: str | None = None, top_n: int = 20, symbols: list[str] | None = None
) -> Path | None:
    user_dir = _find_user_dir(user_id)
    snapshot_date = _latest_snapshot_date(user_dir)

    top_holdings = _load_top_holdings(user_dir, top_n)
    watchlist    = _load_watchlist()

    # Build deduplicated symbol → name map
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    if symbols:
        # If symbols are provided, use them directly
        for sym in symbols:
            if sym.upper() not in seen:
                targets.append((sym.upper(), sym.upper()))
                seen.add(sym.upper())
    else:
        # Default logic: top holdings first, then watchlist additions
        top_holdings = _load_top_holdings(user_dir, top_n)
        watchlist = _load_watchlist()
        for h in top_holdings:
            sym = h.get("tradingsymbol", h.get("symbol", "")).upper()
            if sym and sym not in seen:
                targets.append((sym, sym))
                seen.add(sym)
        for w in watchlist:
            sym = w["symbol"].upper()
            if sym not in seen:
                targets.append((sym, w.get("name", sym)))
                seen.add(sym)

    print(f"\n{'='*60}")
    print(f"  NEWS DIGEST  —  {user_dir.name}  ·  {snapshot_date}")
    print(f"{'='*60}")
    print(f"  Fetching headlines for {len(targets)} stocks (same-day only)...\n")

    results: list[dict] = []
    for i, (sym, name) in enumerate(targets, 1):
        print(f"  [{i:>2}/{len(targets)}] {sym:<16} ", end="", flush=True)
        headlines = _fetch_headlines(sym, name, snapshot_date=snapshot_date)
        results.append({"symbol": sym, "name": name, "headlines": headlines})
        print(f"{len(headlines)} headline(s)")
        time.sleep(FETCH_DELAY)

    digest_md = build_digest(results, snapshot_date, user_dir.name)

    out_dir = user_dir / "holdings" / snapshot_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "news_digest.md"
    out_file.write_text(digest_md, encoding="utf-8")
    print(f"\n✓ News digest saved → {out_file}\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily news digest for top holdings + watchlist")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    parser.add_argument("--top", type=int, default=20, help="Number of top holdings to include (default 20)")
    parser.add_argument("--symbols", nargs="*", help="Specific list of symbols to fetch news for.")
    args = parser.parse_args()
    run(user_id=args.user, top_n=args.top, symbols=args.symbols)
