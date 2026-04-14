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


def _fetch_individual_holders(
    company_id: str,
    quarters: list[str],
) -> dict[str, dict]:
    """
    Call the Screener AJAX endpoint for each classification and return
    a dict keyed by classification label with a list of
    { name, values: { quarter: pct } } investor records.
    """
    results: dict[str, list] = {}
    client = _get_screener_client()
    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    for cls, label in _CLASSIFICATIONS.items():
        url = f"https://www.screener.in/api/3/{company_id}/investors/{cls}/quarterly/"
        try:
            resp = client.get(url, headers=ajax_headers)
            if resp.status_code != 200:
                continue
            raw: dict = resp.json()
        except Exception:
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
            results[label] = investors
    return results


def _fetch_shareholding(screener_url: str) -> dict:
    """
    Scrape the Shareholding Pattern table from Screener's main company page.

    Returns dict with:
        quarters: list of quarter labels (e.g. ["Sep 2025", "Jun 2025", ...])
        rows: list of { category: str, values: list[str] }
        raw_text: plain-text table representation
    """
    base_url = screener_url.rstrip("/")

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

    alerts = []
    for row in rows:
        cat = row.get("category", "")
        matched = next((n for n in all_names if n.lower() in cat.lower()), None)
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
