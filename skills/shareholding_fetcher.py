"""
Skill: Shareholding Fetcher
============================
Scrapes the Shareholding Pattern section from Screener.in for a company
and saves it as a structured text file in data/companies/{SYMBOL}/shareholding/.

Extracts: Promoter, FII, DII, Public holdings across recent quarters.
Saved as: {SYMBOL}_shareholding_{YYYYMMDD}.txt
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from llm.client import get_config
from skills.cdp_helper import fetch_page_html as _cdp_fetch

PROJECT_ROOT = Path(__file__).parent.parent

SKILL_NAME = "shareholding_fetcher"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CDP_URL = "http://127.0.0.1:9222"

# ── Shared Screener session ───────────────────────────────────────────────
_SCREENER_CLIENT: httpx.Client | None = None


def _get_screener_client() -> httpx.Client:
    global _SCREENER_CLIENT
    if _SCREENER_CLIENT is None:
        c = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.screener.in/",
            },
            follow_redirects=True,
            timeout=30,
        )
        try:
            c.get("https://www.screener.in/", timeout=10)  # establish session cookies
        except Exception:
            pass
        _SCREENER_CLIENT = c
    return _SCREENER_CLIENT


def _fetch_page_via_cdp(url: str) -> str | None:
    """Navigate Chrome to URL and return full page HTML. None if CDP unavailable."""
    return _cdp_fetch(url)


def _get_shareholding_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    sh_dir = data_root / symbol / "shareholding"
    sh_dir.mkdir(parents=True, exist_ok=True)
    return sh_dir


_CLASSIFICATIONS = {
    "foreign_institutions": "FIIs (Individual)",
    "domestic_institutions": "DIIs (Individual)",
    "public": "Public Anchor Investors",
    "promoters": "Promoters (Individual)",
}


def _fetch_company_id(soup: BeautifulSoup) -> str | None:
    """
    Extract Screener's internal company ID from the page HTML.
    It appears in: data-url="/trades/company-1274141/"
    """
    import re
    tag = soup.find(attrs={"data-url": re.compile(r"/trades/company-\d+/")})
    if tag:
        m = re.search(r"/trades/company-(\d+)/", tag["data-url"])
        if m:
            return m.group(1)
    return None


AJAX_DELAY = 1.0        # seconds between AJAX requests to avoid Screener rate limits
AJAX_MAX_RETRIES = 2    # retry on failure (429/5xx/timeout)
AJAX_RETRY_BACKOFF = 3  # seconds to wait before retry
INTER_COMPANY_DELAY = 2.0  # seconds to pause between consecutive company fetches (batch mode)


def _fetch_individual_holders(
    company_id: str,
    quarters: list[str],
) -> dict[str, dict]:
    """
    Call the Screener AJAX endpoint for each classification and return
    a dict keyed by classification label with a list of
    { name, values: { quarter: pct } } investor records.

    Includes rate limiting and retry logic to avoid Screener's "too fast" errors.
    """
    results: dict[str, list] = {}
    client = _get_screener_client()
    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    for i, (cls, label) in enumerate(_CLASSIFICATIONS.items()):
        if i > 0:
            time.sleep(AJAX_DELAY)
        url = f"https://www.screener.in/api/3/{company_id}/investors/{cls}/quarterly/"
        raw = None
        for attempt in range(1 + AJAX_MAX_RETRIES):
            try:
                resp = client.get(url, headers=ajax_headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < AJAX_MAX_RETRIES:
                        time.sleep(AJAX_RETRY_BACKOFF * (attempt + 1))
                        continue
                if resp.status_code != 200:
                    break
                raw = resp.json()
                break
            except Exception:
                if attempt < AJAX_MAX_RETRIES:
                    time.sleep(AJAX_RETRY_BACKOFF * (attempt + 1))
                continue

        if not raw:
            continue

        investors = []
        for name, data in raw.items():
            if not isinstance(data, dict):
                continue
            holder_vals = {
                q: data[q] for q in quarters if q in data
            }
            if holder_vals:
                investors.append({"name": name, "values": holder_vals})
        if investors:
            results[label] = _merge_similar_investors(investors)
    return results


def _merge_similar_investors(investors: list[dict]) -> list[dict]:
    """
    Merge investor rows that are spelling variants of the same entity.

    Screener sometimes changes name spellings across quarters (e.g.,
    "Massachusetts Ins. Of Technology" vs "Massachusetts institute of Techno").
    This creates split rows with non-overlapping quarter data.

    Strategy:
      1. Normalize names using _normalize_investor_name.
      2. Also check prefix overlap (first N chars) for truncated names.
      3. Merge values dicts, keeping the higher value when both quarters exist.
      4. Use the longer name as the canonical display name.
    """
    import re

    def _norm(n: str) -> str:
        """Aggressive normalization for grouping: lowercase, expand abbrevs, split camelCase."""
        # Split camelCase BEFORE lowering (e.g., "KotakInfrastructure" → "Kotak Infrastructure")
        s = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
        s = s.lower().strip()
        s = re.sub(r"\bpvt\b\.?", "private", s)
        s = re.sub(r"\bltd\b\.?", "limited", s)
        s = re.sub(r"\bco\b\.?", "company", s)
        s = re.sub(r"\bcorp\b\.?", "corporation", s)
        s = re.sub(r"\bins\b\.?", "institute", s)
        s = re.sub(r"\btechno\b", "technology", s)
        s = re.sub(r"['\"\.\-,\(\)&]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _prefix_key(s: str, length: int = 20) -> str:
        """First N chars of normalized name, for catching truncated variants."""
        return _norm(s)[:length]

    # Group by normalized name or prefix
    groups: dict[str, list[int]] = {}  # key -> list of investor indices
    for idx, inv in enumerate(investors):
        norm = _norm(inv["name"])
        prefix = _prefix_key(inv["name"])
        # Check if this investor matches an existing group by norm or prefix
        matched_key = None
        for key in groups:
            if norm == key or _norm_match(norm, key) or _prefix_key(inv["name"]) == _prefix_key(investors[groups[key][0]]["name"]):
                matched_key = key
                break
        if matched_key:
            groups[matched_key].append(idx)
        else:
            groups[norm] = [idx]

    merged = []
    for key, indices in groups.items():
        if len(indices) == 1:
            merged.append(investors[indices[0]])
            continue
        # Merge: prefer name from the variant with the most recent quarter data, fallback to longest
        # (the latest data's name is more likely to be the current/correct spelling)
        _MONTH_ORDER = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

        def _quarter_sort_key(q: str) -> tuple:
            parts = q.strip().split()
            if len(parts) == 2:
                return (int(parts[1]), _MONTH_ORDER.get(parts[0].lower()[:3], 0))
            return (0, 0)

        latest_q_name = max(
            (investors[i] for i in indices),
            key=lambda inv: max((_quarter_sort_key(q) for q in inv["values"]), default=(0, 0)),
        )["name"]
        canonical_name = latest_q_name if latest_q_name else max((investors[i]["name"] for i in indices), key=len)
        merged_vals: dict = {}
        for i in indices:
            for q, v in investors[i]["values"].items():
                existing = merged_vals.get(q)
                if existing is None or (not existing and v) or (v and float(v or 0) > float(existing or 0)):
                    merged_vals[q] = v
        merged.append({"name": canonical_name, "values": merged_vals})
    return merged


def _norm_match(a: str, b: str) -> bool:
    """Check if two normalized names are likely the same entity."""
    if a == b:
        return True
    # One contains the other (handles truncation)
    if a in b or b in a:
        return True
    # Check word overlap: if 70%+ of words match
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    return overlap / min(len(wa), len(wb)) >= 0.7


CDP_RETRY_DELAY = 3.0   # seconds to wait before retrying CDP fetch


def _fetch_shareholding(screener_url: str) -> dict:
    """
    Scrape the Shareholding Pattern table from Screener's main company page.

    Returns dict with:
        quarters: list of quarter labels (e.g. ["Sep 2025", "Jun 2025", ...])
        rows: list of { category: str, values: list[str] }
        raw_text: plain-text table representation

    Retries once if CDP returns HTML but the shareholding section is missing
    (handles transient page-load failures / Screener rate-limit pages).
    """
    base_url = screener_url.rstrip("/")

    for attempt in range(2):
        # Chrome CDP only — httpx bulk scraping causes IP bans
        html = _fetch_page_via_cdp(base_url)
        if not html:
            raise RuntimeError(
                f"Chrome CDP not available or failed to fetch {base_url}. "
                "Launch Chrome debug session before running batch."
            )
        soup = BeautifulSoup(html, "lxml")
        company_id = _fetch_company_id(soup)

        # Screener renders shareholding in section with id="shareholding"
        section = soup.find(id="shareholding")
        if section:
            break
        # Retry once — may have hit a rate-limit/error page
        if attempt == 0:
            print(f"    [shareholding_fetcher] No shareholding section on first attempt — retrying in {CDP_RETRY_DELAY}s...")
            time.sleep(CDP_RETRY_DELAY)

    if not section:
        return {}

    table = section.find("table")
    if not table:
        return {}

    quarters: list[str] = []
    rows: list[dict] = []
    lines: list[str] = []

    # Parse header row for quarter labels
    thead = table.find("thead")
    if thead:
        header_cells = thead.find_all(["th", "td"])
        quarters = [c.get_text(strip=True) for c in header_cells[1:]]
        lines.append("| Category | " + " | ".join(quarters) + " |")
        lines.append("|" + "---|" * (len(quarters) + 1))

    # Parse body rows
    tbody = table.find("tbody")
    if tbody:
        for tr in tbody.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            category = cells[0].get_text(strip=True)
            values = [c.get_text(strip=True) for c in cells[1:]]
            rows.append({"category": category, "values": values})
            lines.append(f"| {category} | " + " | ".join(values) + " |")

    # Fetch individual holders via AJAX if we have the company ID
    individual: dict = {}
    if company_id:
        print(f"    [shareholding_fetcher] Fetching individual holders (company_id={company_id})...")
        individual = _fetch_individual_holders(company_id, quarters)
        for label, investors in individual.items():
            print(f"    [shareholding_fetcher]   {label}: {len(investors)} holder(s)")

    return {
        "quarters": quarters,
        "rows": rows,
        "raw_text": "\n".join(lines),
        "individual": individual,
    }


def _load_tracked_investors() -> dict:
    """Load investor_watchlist.yaml if present. Returns the tracked_investors sub-dict."""
    wl_path = PROJECT_ROOT / "investor_watchlist.yaml"
    if not wl_path.exists():
        return {}
    import yaml
    with open(wl_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("tracked_investors", data)


def _normalize_investor_name(name: str) -> str:
    """Normalize investor name for fuzzy matching.
    Handles: Pvt/Private, Ltd/Limited, Ins/Institute, camelCase, punctuation, extra whitespace."""
    import re
    # Split camelCase before lowering (e.g., "KotakInfrastructure" → "Kotak Infrastructure")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = s.lower().strip()
    # Normalize common abbreviations
    s = re.sub(r"\bpvt\b\.?", "private", s)
    s = re.sub(r"\bltd\b\.?", "limited", s)
    s = re.sub(r"\bco\b\.?", "company", s)
    s = re.sub(r"\bcorp\b\.?", "corporation", s)
    s = re.sub(r"\bins\b\.?", "institute", s)
    s = re.sub(r"\btechno\b", "technology", s)
    s = re.sub(r"\bllp\b", "llp", s)
    # Remove extra punctuation and whitespace
    s = re.sub(r"['\"\.\-,&\(\)]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_tracked_investors(rows: list[dict], quarters: list[str]) -> list[dict]:
    """
    Check if any row category matches a tracked investor.
    Returns list of alerts: { investor, category, latest_pct, prev_pct, trend }
    """
    tracked = _load_tracked_investors()
    all_names = []
    for group in tracked.values():
        if isinstance(group, list):
            all_names.extend(group)

    # Pre-normalize all tracked names
    normalized_map = {_normalize_investor_name(n): n for n in all_names}

    from difflib import SequenceMatcher

    def _fuzzy_match(cat_norm: str, normalized_map: dict) -> str | None:
        """Match by substring, then by character-level similarity (≥0.85 ratio)."""
        # Exact/substring match first
        for norm, orig in normalized_map.items():
            if norm in cat_norm or cat_norm in norm:
                return orig
        # Fuzzy fallback for minor spelling variants (Agarwal/Agrawal etc.)
        for norm, orig in normalized_map.items():
            if SequenceMatcher(None, cat_norm, norm).ratio() >= 0.85:
                return orig
        return None

    alerts = []
    for row in rows:
        cat = row.get("category", "")
        cat_norm = _normalize_investor_name(cat)
        matched = _fuzzy_match(cat_norm, normalized_map)
        if matched:
            vals = row.get("values", [])
            latest = vals[-1] if vals else "N/A"
            prev = vals[-2] if len(vals) > 1 else "N/A"
            # Determine trend
            try:
                l_f = float(latest.replace("%", ""))
                p_f = float(prev.replace("%", ""))
                if l_f > p_f + 0.1:
                    trend = "INCREASING"
                elif l_f < p_f - 0.1:
                    trend = "DECREASING"
                else:
                    trend = "STABLE"
            except Exception:
                trend = "UNKNOWN"
            alerts.append({
                "investor": matched,
                "category": cat,
                "latest_pct": latest,
                "prev_pct": prev,
                "trend": trend,
            })
    return alerts


def _format_report(symbol: str, company_name: str, data: dict) -> str:
    today = datetime.now().strftime("%d %b %Y")
    lines = [
        f"# Shareholding Pattern — {company_name} ({symbol})",
        f"*Fetched: {today} | Source: Screener.in*\n",
    ]

    quarters = data.get("quarters", [])
    rows = data.get("rows", [])

    if quarters:
        lines.append(f"## Quarters Covered\n{', '.join(quarters)}\n")

    # Highlight key categories
    KEY_CATS = {"Promoters", "FIIs", "DIIs", "Public", "Promoter & Promoter Group",
                "Foreign Institutional Investors", "Domestic Institutional Investors",
                "Mutual Funds", "Insurance Companies", "Government", "Others"}

    lines.append("## Full Shareholding Table\n")
    lines.append(data.get("raw_text", "No data"))
    lines.append("")

    # Key holders summary
    key_rows = [r for r in rows if any(k.lower() in r["category"].lower()
                                        for k in ["promoter", "fii", "dii", "public",
                                                  "mutual", "insurance", "foreign",
                                                  "domestic"])]
    if key_rows and quarters:
        lines.append("## Key Holdings Summary (Most Recent Quarter)\n")
        latest_q = quarters[-1] if quarters else "Latest"
        lines.append(f"*Quarter: {latest_q}*\n")
        for r in key_rows:
            latest_val = r["values"][-1] if r["values"] else "N/A"
            lines.append(f"- **{r['category']}**: {latest_val}")
        lines.append("")

    # ── Individual holder detail ──────────────────────────────────────────
    individual = data.get("individual", {})
    latest_q = quarters[-1] if quarters else ""
    prev_q = quarters[-2] if len(quarters) > 1 else ""

    if individual:
        lines.append("## Individual Holder Detail\n")
        for label, investors in individual.items():
            lines.append(f"### {label}")
            header = f"| Investor | {prev_q} | {latest_q} | Trend |"
            lines.append(header)
            lines.append("|---|---|---|---|")
            for inv in sorted(investors, key=lambda x: float(x["values"].get(latest_q, 0) or 0), reverse=True):
                lv = inv["values"].get(latest_q, "—")
                pv = inv["values"].get(prev_q, "—")
                try:
                    trend = "⬆" if float(lv) > float(pv) + 0.01 else ("⬇" if float(lv) < float(pv) - 0.01 else "→")
                except Exception:
                    trend = "—"
                lines.append(f"| {inv['name']} | {pv}% | {lv}% | {trend} |")
            lines.append("")

    # ── Tracked investor alerts ───────────────────────────────────────────
    # Match against both aggregated rows AND individual holder names
    individual_rows: list[dict] = []
    for label, investors in individual.items():
        for inv in investors:
            vals_list = [inv["values"].get(q, "") for q in quarters]
            individual_rows.append({"category": inv["name"], "values": vals_list})

    alerts = _match_tracked_investors(rows + individual_rows, quarters)
    if alerts:
        lines.append("## Tracked Investor Alerts\n")
        for a in alerts:
            trend_icon = {"INCREASING": "⬆", "DECREASING": "⬇", "STABLE": "→"}.get(a["trend"], "?")
            lines.append(
                f"- {trend_icon} **{a['category']}** — "
                f"latest: {a['latest_pct']}  prev: {a['prev_pct']}  trend: {a['trend']}"
            )
        lines.append("")
    else:
        lines.append("## Tracked Investor Alerts\n")
        lines.append("*No tracked investors from your watchlist found in this shareholding data.*\n")

    return "\n".join(lines)


def run(company: dict, config: dict) -> dict:
    """
    Fetch shareholding pattern from Screener for a company.

    Args:
        company: Entry from watchlist.yaml (must have screener_url)
        config:  Loaded config.yaml dict

    Returns:
        dict with: skill_name, symbol, status, data, error
    """
    symbol = company["symbol"]
    company_name = company.get("name", symbol)
    screener_url = company.get("screener_url", "")

    result = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {"saved": None},
        "error": None,
    }

    if not screener_url:
        result["status"] = "error"
        result["error"] = "No screener_url configured for this company."
        print(f"[{SKILL_NAME}][{symbol}] No screener_url — cannot fetch.")
        return result

    try:
        sh_dir = _get_shareholding_dir(symbol)
        today_str = datetime.now().strftime("%Y%m%d")
        filename = f"{symbol}_shareholding_{today_str}.txt"
        dest_path = sh_dir / filename

        if dest_path.exists():
            print(f"[{SKILL_NAME}][{symbol}] Already exists: {filename} — skipping.")
            result["data"]["saved"] = str(dest_path)
            return result

        print(f"[{SKILL_NAME}][{symbol}] Fetching shareholding from Screener...")
        data = _fetch_shareholding(screener_url)

        if not data or not data.get("rows"):
            result["status"] = "no_data"
            result["error"] = "No shareholding table found on Screener page."
            print(f"[{SKILL_NAME}][{symbol}] No shareholding data found.")
            return result

        n_quarters = len(data.get("quarters", []))
        n_rows = len(data.get("rows", []))
        print(f"[{SKILL_NAME}][{symbol}] Parsed {n_rows} rows across {n_quarters} quarters.")

        report = _format_report(symbol, company_name, data)
        dest_path.write_text(report, encoding="utf-8")
        result["data"]["saved"] = str(dest_path)
        result["data"]["quarters"] = data.get("quarters", [])
        result["data"]["rows"] = n_rows
        print(f"[{SKILL_NAME}][{symbol}] Saved: {filename}")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")

    return result


if __name__ == "__main__":
    import sys
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.shareholding_fetcher <SYMBOL>")
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
    if fetch_result["data"].get("saved"):
        print(f"  Saved: {fetch_result['data']['saved']}")
