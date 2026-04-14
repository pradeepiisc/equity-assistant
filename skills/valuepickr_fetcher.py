"""
ValuePickr Forum Fetcher
========================
Fetches expert analysis and discussion threads from ValuePickr forum
(https://forum.valuepickr.com) for a given company.

Uses the Discourse JSON API — no auth required for public posts.

Usage:
    python -m skills.valuepickr_fetcher EIEL
    python -m skills.valuepickr_fetcher EIEL --name "Enviro Infra Engineering"
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://forum.valuepickr.com"

# Words excluded from name-match validation — too common to be distinctive
_GENERIC_WORDS = {
    "LTD", "LIMITED", "PRIVATE", "PVT", "COMPANY", "AND", "THE",
    "OF", "FOR", "NEW", "GLOBAL", "INTERNATIONAL", "NATIONAL",
}
SEARCH_URL = f"{BASE_URL}/search.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/html",
}
REQUEST_DELAY = 0.8          # seconds between requests (be polite)
MAX_POSTS_FETCH = 60         # max posts to scan per thread
MAX_TOP_POSTS = 10           # top liked posts to include in output
EARLY_POSTS_COUNT = 7        # include posts #2–#N as early discussion
LATEST_POSTS_COUNT = 5       # include last N posts as recent updates


def _search_topic(query: str, symbol: str, interactive: bool = True) -> dict | None:
    """Search ValuePickr for the most relevant company thread."""
    sym_up = symbol.upper()
    query_words = [w for w in query.upper().split() if len(w) > 3]
    # Distinctive words = company name words that are NOT generic suffixes/country names
    specific_words = [w for w in query_words if w not in _GENERIC_WORDS]

    def _score(t: dict) -> int:
        title = t.get("title", "").upper()
        score = 0
        if sym_up in title:
            score += 10
        for w in query_words:
            if w in title:
                score += 2
        score += min(t.get("posts_count", 0) // 10, 5)  # weight by engagement
        return score

    def _is_valid_match(t: dict) -> bool:
        """Require ALL distinctive company name words to appear in the topic title,
        OR the symbol itself to appear. Prevents 'Rossell India' matching 'Rossell Techsys'."""
        title = t.get("title", "").upper()
        if sym_up in title:
            return True
        if not specific_words:
            # No distinctive words after filtering — fall back to any score > 2
            return _score(t) > 2
        # Every distinctive word must be present in the title
        return all(w in title for w in specific_words)

    # Try: with category filter first (higher precision), then without (broader recall)
    search_variants = [
        f"{query} category:stock-opportunities",
        query,
        f"{symbol} category:stock-opportunities",
        symbol,
    ]

    all_valid_matches: list[dict] = []
    for search_term in search_variants:
        params = {"q": search_term}
        try:
            r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception:
            time.sleep(REQUEST_DELAY)
            continue

        topics = data.get("topics") or []
        if not topics:
            time.sleep(REQUEST_DELAY)
            continue

        topics.sort(key=_score, reverse=True)
        for candidate in topics:
            if _score(candidate) > 0 and _is_valid_match(candidate):
                # Dedupe by topic_id
                if not any(m["id"] == candidate["id"] for m in all_valid_matches):
                    all_valid_matches.append(candidate)
        time.sleep(REQUEST_DELAY)

    if not all_valid_matches:
        return None

    # Single match — return it directly
    if len(all_valid_matches) == 1:
        return all_valid_matches[0]

    # Multiple matches — interactive selection if enabled
    if not interactive:
        # Non-interactive: return highest-scored match
        return all_valid_matches[0]

    # Show top 5 matches and let user choose
    print(f"\n  Multiple ValuePickr threads found for '{query}':")
    display_count = min(5, len(all_valid_matches))
    for i, t in enumerate(all_valid_matches[:display_count], 1):
        title = t.get("title", "")
        posts = t.get("posts_count", 0)
        topic_id = t["id"]
        print(f"    [{i}] {title} ({posts} posts) — ID: {topic_id}")
    
    print(f"    [0] Skip — no correct match")
    
    while True:
        try:
            choice = input(f"\n  Select thread [0-{display_count}]: ").strip()
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= display_count:
                return all_valid_matches[idx - 1]
            print(f"  Invalid choice. Enter 0-{display_count}.")
        except (ValueError, KeyboardInterrupt):
            print("\n  Skipping selection.")
            return None


def _clean_html(cooked: str) -> str:
    """Strip HTML tags from Discourse cooked content, removing quoted replies."""
    soup = BeautifulSoup(cooked or "", "lxml")
    for el in soup.find_all("blockquote"):
        el.decompose()
    for el in soup.find_all("aside"):  # Discourse quote wrappers
        el.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_topic_posts(topic_id: int, max_posts: int = MAX_POSTS_FETCH) -> list[dict]:
    """Fetch posts from a topic, paginating until max_posts reached."""
    all_posts: list[dict] = []
    url = f"{BASE_URL}/t/{topic_id}.json"
    page = 1
    highest_post_number = None

    while len(all_posts) < max_posts:
        params = {} if page == 1 else {"page": page}
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 404:
                break
            r.raise_for_status()
            data = r.json()
        except Exception:
            break

        page_posts = (data.get("post_stream") or {}).get("posts") or []
        if not page_posts:
            break
        all_posts.extend(page_posts)

        # Track highest post number to know when we've fetched everything
        if highest_post_number is None:
            highest_post_number = data.get("highest_post_number", 0)
        
        # Get the highest post_number we've fetched so far
        max_fetched = max((p.get("post_number", 0) for p in all_posts), default=0)
        
        # Stop if: we've reached the highest post, or this page had < 20 posts (last page)
        if max_fetched >= highest_post_number or len(page_posts) < 20:
            break
        
        page += 1
        time.sleep(REQUEST_DELAY)

    return all_posts[:max_posts]


def _format_post(post: dict) -> str:
    text = _clean_html(post.get("cooked", ""))
    if not text:
        return ""
    dt = (post.get("created_at") or "")[:10]
    user = post.get("username", "?")
    likes = post.get("like_count", 0)
    num = post.get("post_number", "?")
    header = f"**#{num} — {user}** | {dt} | 👍 {likes}"
    return f"{header}\n\n{text}"


def run(
    symbol: str,
    company_name: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
    interactive: bool = True,
) -> Path | None:
    """
    Fetch ValuePickr thread for the given company and save as markdown.
    Returns path to saved file, or None if no thread found.
    
    Args:
        interactive: If True and multiple valid matches exist, prompt user to select.
    """
    search_query = company_name or symbol
    if verbose:
        print(f"  Searching ValuePickr for: '{search_query}'")

    topic = _search_topic(search_query, symbol, interactive=interactive)
    if not topic:
        if verbose:
            print(f"  [valuepickr] No thread found for {symbol}")
        return None

    topic_id    = topic["id"]
    topic_title = topic.get("title", "")
    topic_slug  = topic.get("slug", str(topic_id))
    posts_count = topic.get("posts_count", 0)
    topic_url   = f"{BASE_URL}/t/{topic_slug}/{topic_id}"

    if verbose:
        print(f"  Found: '{topic_title}' — {posts_count} posts")
        print(f"  URL: {topic_url}")

    time.sleep(REQUEST_DELAY)
    all_posts = _fetch_topic_posts(topic_id)
    if not all_posts:
        if verbose:
            print(f"  [valuepickr] Thread found but posts not accessible")
        return None

    first_post = all_posts[0]
    rest = all_posts[1:]

    # Prefer liked posts; fall back to early + latest when likes unavailable
    top_liked = sorted(
        [p for p in rest if p.get("like_count", 0) > 0],
        key=lambda p: -p["like_count"],
    )[:MAX_TOP_POSTS]

    if not top_liked:
        # For small threads, include all posts; for larger threads, use early + latest strategy
        if len(rest) <= EARLY_POSTS_COUNT + LATEST_POSTS_COUNT:
            # Thread is small enough - include all posts
            supplemental = rest
        else:
            # Large thread - use early + latest strategy
            early = rest[:EARLY_POSTS_COUNT]
            latest = rest[-LATEST_POSTS_COUNT:]
            # dedupe by post_number
            seen = {p["post_number"] for p in early}
            latest = [p for p in latest if p["post_number"] not in seen]
            supplemental = early + latest
    else:
        supplemental = []

    fetched_at = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = [
        f"# ValuePickr: {topic_title}",
        f"**Thread**: {topic_url}  ",
        f"**Total posts**: {posts_count}  |  **Posts scanned**: {len(all_posts)}  |  **Fetched**: {fetched_at}\n",
        "---\n",
        "## Original Post (Thesis / Introduction)\n",
        _format_post(first_post),
        "\n---\n",
    ]

    if top_liked:
        lines.append(f"## Top {len(top_liked)} Most-Liked Posts\n")
        for post in top_liked:
            fmt = _format_post(post)
            if fmt:
                lines.append(fmt)
                lines.append("\n---\n")
    elif supplemental:
        lines.append(f"## Discussion Highlights (early + recent posts)\n")
        for post in supplemental:
            fmt = _format_post(post)
            if fmt:
                lines.append(fmt)
                lines.append("\n---\n")

    md_content = "\n".join(lines)

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = Path(__file__).parent.parent / "data" / "companies" / symbol.upper()
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "valuepickr.md"
    out_path.write_text(md_content, encoding="utf-8")

    meta_path = out_dir / "valuepickr.json"
    meta_path.write_text(
        json.dumps({
            "symbol": symbol,
            "topic_id": topic_id,
            "topic_title": topic_title,
            "topic_url": topic_url,
            "posts_count": posts_count,
            "posts_scanned": len(all_posts),
            "top_liked_included": len(top_liked),
            "fetched_at": fetched_at,
        }, indent=2),
        encoding="utf-8",
    )

    if verbose:
        print(f"  ✓ Saved → {out_path}")
        extra = f"{len(top_liked)} top-liked" if top_liked else f"{len(supplemental)} discussion posts (early+recent)"
        print(f"    {len(all_posts)} posts scanned, {extra} included")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch ValuePickr forum analysis for a company")
    parser.add_argument("symbol", help="Trading symbol e.g. EIEL")
    parser.add_argument("--name", help="Company name override for search")
    parser.add_argument("--out", help="Output directory (default: data/companies/{SYMBOL})")
    args = parser.parse_args()

    try:
        from skills.company_meta import get_company_name  # noqa: PLC0415
        name = args.name or get_company_name(args.symbol, args.symbol)
    except Exception:
        name = args.name or args.symbol

    out_dir = Path(args.out) if args.out else None
    path = run(symbol=args.symbol, company_name=name, output_dir=out_dir, verbose=True)
    if not path:
        print("No ValuePickr thread found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
