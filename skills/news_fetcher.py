"""
Skill: News Fetcher
====================
Fetches recent news articles for a company from Google News RSS and saves
them as individual .txt files in data/companies/{SYMBOL}/news/.

Uses the company's full name for better search coverage.
Each article saved as: {SYMBOL}_news_{YYYYMMDD}_{n}.txt
"""

import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

from llm.client import get_config

PROJECT_ROOT = Path(__file__).parent.parent

SKILL_NAME = "news_fetcher"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DOWNLOAD_DELAY = 0.5


def _get_news_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    news_dir = data_root / symbol / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    return news_dir


def _fetch_google_news_rss(company_name: str, symbol: str, limit: int = 5) -> list[dict]:
    """
    Fetch articles from Google News RSS for the company.
    Returns list of dicts: title, link, published, source, summary.
    """
    # Use full company name only — appending the ticker abbreviation causes noise
    # from unrelated companies that share similar short codes.
    # Suppress news fetching for J&KBANK
    if symbol.upper() == "J&KBANK":
        return []
    search_term = company_name if company_name and company_name != symbol else symbol
    query = search_term.replace(" ", "+")
    rss_url = (
        f"https://news.google.com/rss/search"
        f"?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    )

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        response = client.get(rss_url, headers=headers)
        response.raise_for_status()

    root = ET.fromstring(response.text)
    ns = {"media": "http://search.yahoo.com/mrss/"}
    articles = []

    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        source_tag = item.find("source")
        source = source_tag.text.strip() if source_tag is not None else "Unknown"
        description = item.findtext("description", "").strip()
        # Strip HTML tags from description
        summary = re.sub(r"<[^>]+>", "", description).strip()

        articles.append({
            "title": title,
            "link": link,
            "published": pub_date,
            "source": source,
            "summary": summary,
        })

        if len(articles) >= limit:
            break

    return articles


def _parse_date(pub_date: str) -> str:
    """Parse RFC 2822 date to YYYYMMDD string for filename."""
    try:
        dt = datetime.strptime(pub_date[:25], "%a, %d %b %Y %H:%M:%S")
        return dt.strftime("%Y%m%d")
    except Exception:
        return datetime.now().strftime("%Y%m%d")


CONTENT_MAX_CHARS = 4000
FETCH_TIMEOUT = 15


def _scrape_article_content(url: str) -> str:
    """
    Attempt to fetch and extract the full article text from the URL.
    Uses trafilatura if available; falls back to BeautifulSoup <p> extraction.
    Returns empty string if fetch fails (paywall / timeout / bot-block).
    """
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        }
        with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return ""
            html = resp.text

        if _HAS_TRAFILATURA:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            return (text or "")[:CONTENT_MAX_CHARS]

        # BeautifulSoup fallback
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        # Remove nav/header/footer/script noise
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()
        article_tag = soup.find("article") or soup.find("main") or soup.body
        if article_tag:
            paragraphs = article_tag.find_all("p")
            text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
            return text[:CONTENT_MAX_CHARS]
        return ""
    except Exception:
        return ""


def _format_article(symbol: str, article: dict, n: int) -> str:
    content = article.get("content", "").strip()
    summary = article.get("summary", "").strip()
    body = content if content else summary
    return (
        f"# {article['title']}\n\n"
        f"**Source:** {article['source']}\n"
        f"**Published:** {article['published']}\n"
        f"**Link:** {article['link']}\n\n"
        f"## Content\n\n{body}\n"
    )


def run(company: dict, config: dict, limit: int = 5) -> dict:
    """
    Fetch recent news articles for a company via Google News RSS.

    Args:
        company: Entry from watchlist.yaml (must have 'name' and 'symbol')
        config:  Loaded config.yaml dict
        limit:   Max articles to fetch (default 5)

    Returns:
        dict with: skill_name, symbol, status, data, error
    """
    symbol = company["symbol"]
    company_name = company.get("name", symbol)

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {"saved": [], "skipped": [], "failed": []},
        "error": None,
    }

    try:
        news_dir = _get_news_dir(symbol)
        print(f"[{SKILL_NAME}][{symbol}] Searching news for: {company_name}")

        articles = _fetch_google_news_rss(company_name, symbol, limit=limit)

        if not articles:
            result["status"] = "no_data"
            result["error"] = "No articles found via Google News RSS."
            print(f"[{SKILL_NAME}][{symbol}] No articles found.")
            return result

        print(f"[{SKILL_NAME}][{symbol}] Found {len(articles)} article(s).")

        for n, article in enumerate(articles, start=1):
            date_str = _parse_date(article["published"])
            filename = f"{symbol}_news_{date_str}_{n:02d}.txt"
            dest_path = news_dir / filename

            if dest_path.exists():
                print(f"[{SKILL_NAME}][{symbol}] Skipping (exists): {filename}")
                result["data"]["skipped"].append(str(dest_path))
                continue

            # Attempt to scrape full article content
            full_text = _scrape_article_content(article["link"])
            if full_text:
                article["content"] = full_text
                print(f"[{SKILL_NAME}][{symbol}] ({n}) {article['source']} — {len(full_text)} chars scraped")
            else:
                print(f"[{SKILL_NAME}][{symbol}] ({n}) {article['source']} — paywall/blocked, using RSS summary")

            content = _format_article(symbol, article, n)
            dest_path.write_text(content, encoding="utf-8")
            result["data"]["saved"].append(str(dest_path))
            print(f"[{SKILL_NAME}][{symbol}] Saved: {filename}")
            time.sleep(DOWNLOAD_DELAY)

        n_saved = len(result["data"]["saved"])
        n_skipped = len(result["data"]["skipped"])
        print(f"[{SKILL_NAME}][{symbol}] Done. Saved={n_saved}, Skipped={n_skipped}")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")
        traceback.print_exc()

    return result


if __name__ == "__main__":
    import sys
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.news_fetcher <SYMBOL>")
        sys.exit(1)

    target_symbol = sys.argv[1].upper()
    watchlist_path = PROJECT_ROOT / "watchlist.yaml"
    with open(watchlist_path) as f:
        wl_data = yaml.safe_load(f)

    target = next(
        (c for c in wl_data.get("stocks", []) if c.get("symbol", "").upper() == target_symbol),
        None,
    )
    if target is None:
        print(f"[ERROR] Symbol '{target_symbol}' not found in watchlist.yaml.")
        sys.exit(1)

    cfg = get_config()
    fetch_result = run(target, cfg)
    print(f"\nResult: {fetch_result['status']}")
    print(f"  Saved:   {len(fetch_result['data'].get('saved', []))} file(s)")
    print(f"  Skipped: {len(fetch_result['data'].get('skipped', []))} file(s)")
