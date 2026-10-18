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

    hf = holdings_dir / "holdings.json"
    if hf.exists():
        holdings = [h for h in json.loads(hf.read_text()) if h.get("quantity", 0) > 0]
        # Compute current_value if not present
        for h in holdings:
            if "current_value" not in h:
                h["current_value"] = h.get("last_price", 0) * h.get("quantity", 0)
        return sorted(holdings, key=lambda x: -x["current_value"])[:top_n]
    return []


def _latest_snapshot_date(user_dir: Path) -> str:
    holdings_dir = user_dir / "holdings"
    running_csv = holdings_dir / "running_holdings.csv"
    if running_csv.exists():
        import pandas as pd
        df = pd.read_csv(running_csv, nrows=0)
        date_cols = [c for c in df.columns if c not in ('symbol', 'quantity', 'avg_price')]
        if date_cols:
            return date_cols[-1]
            
    from datetime import date
    return date.today().isoformat()


def _load_watchlist() -> list[dict]:
    path = PROJECT_ROOT / "watchlist.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


# ── news fetcher ───────────────────────────────────────────────────────────────

SUPER_INVESTOR_TRACKERS = [
    {
        "name": "Ashish Kacholia",
        "entity": "Lucky Securities / Bengal Finance & Investment",
        "query": '"Ashish Kacholia" OR "Bengal Finance"',
    },
    {
        "name": "Mukul Agrawal",
        "entity": "Param Capital",
        "query": '"Mukul Agrawal" OR "Mukul Agarwal" OR "Param Capital"',
    },
    {
        "name": "Madhusudan Kela",
        "entity": "MK Ventures / Singularity AMC / Singularity Equity",
        "query": '"Madhusudan Kela" OR "MK Ventures" OR "Singularity AMC"',
    },
    {
        "name": "Vijay Kedia",
        "entity": "Kedia Securities",
        "query": '"Vijay Kedia" OR "Kedia Securities"',
    },
    {
        "name": "Sunil Singhania",
        "entity": "Abakkus Asset Manager",
        "query": '"Sunil Singhania" OR "Abakkus"',
    },
]

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


def _is_within_days(pub_raw: str, ref_date_str: str, max_days: int = 14) -> bool:
    """Check if RSS pubDate is within max_days of ref_date_str (in IST)."""
    try:
        pub_dt = parsedate_to_datetime(pub_raw)
        if pub_dt is None:
            return True
        pub_date = pub_dt.astimezone(ZoneInfo("Asia/Kolkata")).date() if pub_dt.tzinfo else pub_dt.date()
        ref_date = datetime.strptime(ref_date_str, "%Y-%m-%d").date()
        delta = (ref_date - pub_date).days
        return 0 <= delta <= max_days
    except Exception:
        return True


def _fetch_super_investor_headlines(
    tracker: dict,
    limit: int = 5,
    snapshot_date: str | None = None,
    max_days: int = 14,
) -> list[dict]:
    """Fetch headlines from Google News RSS for super investors."""
    import urllib.parse
    encoded_q = urllib.parse.quote(tracker["query"])
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-IN&gl=IN&ceid=IN:en"
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
            if snapshot_date and not _is_within_days(pub_raw, snapshot_date, max_days=max_days):
                continue

            pub = pub_raw[:16]
            link = item.findtext("link", "")
            articles.append({"title": title, "source": source, "published": pub, "link": link})

            if len(articles) >= limit:
                break
        return articles
    except Exception:
        return []


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
    super_investor_results: list[dict], # [{name, entity, headlines: [{title, source, published, link}]}]
    snapshot_date: str,
    user_id: str,
) -> str:
    lines = [
        "# Daily News Digest",
        f"**{user_id}  ·  {snapshot_date}**\n",
    ]

    # 1. Super Investor & Anchor Activity Radar
    lines.append("## 🌟 Super Investor & Anchor Activity Radar")
    lines.append("*Tracking IPOs, Anchor Allotments, Pre-IPO stakes, and Fresh Deals by Ashish Kacholia, Mukul Agrawal, Madhusudan Kela, Vijay Kedia, and Sunil Singhania (Last 30 Days)*\n")

    has_super_news = False
    for si in super_investor_results:
        if si["headlines"]:
            has_super_news = True
            lines.append(f"### {si['name']} — *{si['entity']}*")
            for h in si["headlines"]:
                age = _age_label(h["published"])
                src = h["source"]
                title = h["title"]
                link = h["link"]
                lines.append(f"- [{title}]({link})  *{src} · {age}*")
            lines.append("")

    if not has_super_news:
        lines.append("*No new IPO or bulk investment filings tracked in the last 30 days for tracked anchor investors.*\n")

    lines.append("---\n")

    # 2. Portfolio & Watchlist News
    lines.append("## 📊 Portfolio & Watchlist Company News")
    lines.append(f"*{sum(len(r['headlines']) for r in results)} same-day headlines across {len(results)} stocks*\n")

    no_news = []
    for r in results:
        if not r["headlines"]:
            no_news.append(r["symbol"])
            continue
        lines.append(f"### {r['symbol']}  —  {r['name']}")
        for h in r["headlines"]:
            age = _age_label(h["published"])
            src = h["source"]
            title = h["title"]
            link = h["link"]
            lines.append(f"- [{title}]({link})  *{src} · {age}*")
        lines.append("")

    if no_news:
        lines.append(f"*No same-day news found for: {', '.join(no_news)}*\n")

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

    cache_file = PORTFOLIO_DIR / ".cache" / f"news_{snapshot_date}.json"
    cache: dict = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except Exception:
            pass

    # 1. Fetch Super Investor News
    print(f"  Fetching Super Investor & Anchor activity (last 30 days)...")
    super_investor_results: list[dict] = []
    super_cache = cache.get("__SUPER_INVESTORS__", {})
    for tracker in SUPER_INVESTOR_TRACKERS:
        tname = tracker["name"]
        if tname in super_cache:
            si_headlines = super_cache[tname]
            print(f"    • {tname:<20} {len(si_headlines)} headline(s) (cached)")
        else:
            si_headlines = _fetch_super_investor_headlines(tracker, snapshot_date=snapshot_date, max_days=30)
            super_cache[tname] = si_headlines
            print(f"    • {tname:<20} {len(si_headlines)} headline(s)")
            time.sleep(FETCH_DELAY)
        super_investor_results.append({
            "name": tname,
            "entity": tracker["entity"],
            "headlines": si_headlines,
        })
    cache["__SUPER_INVESTORS__"] = super_cache

    # 2. Fetch Holdings & Watchlist News
    print(f"\n  Fetching headlines for {len(targets)} stocks (same-day only)...")
    results: list[dict] = []
    for i, (sym, name) in enumerate(targets, 1):
        print(f"  [{i:>2}/{len(targets)}] {sym:<16} ", end="", flush=True)
        if sym in cache:
            headlines = cache[sym]
            print(f"{len(headlines)} headline(s) (cached)")
        else:
            headlines = _fetch_headlines(sym, name, snapshot_date=snapshot_date)
            cache[sym] = headlines
            print(f"{len(headlines)} headline(s)")
            time.sleep(FETCH_DELAY)
        
        results.append({"symbol": sym, "name": name, "headlines": headlines})

    # Save updated cache
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    digest_md = build_digest(results, super_investor_results, snapshot_date, user_dir.name)

    out_file = user_dir / "news_digest.md"
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
