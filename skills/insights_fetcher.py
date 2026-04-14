"""
Skill: Insights Fetcher
========================
Scrapes the "Insights" section (Beta) from a company's Screener.in page
and saves the findings as a structured text file.

Screener shows company-specific insights like:
  - Revenue / profit momentum
  - Promoter holding changes
  - Debt reduction / free cash flow
  - Return ratios trends
  - Custom sector metrics

Saves as: data/companies/{SYMBOL}/insights/{SYMBOL}_insights_{YYYYMMDD}.txt

Run standalone:
    python -m skills.insights_fetcher SYMBOL
"""

import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from llm.client import get_config
from skills.cdp_helper import fetch_page_html as _cdp_fetch
from skills.cdp_helper import is_available as _chrome_available
from skills.company_meta import store_company_meta

PROJECT_ROOT = Path(__file__).parent.parent

SKILL_NAME = "insights_fetcher"

# ── SCREENER SCRAPING POLICY ───────────────────────────────────────────────────
# Chrome CDP is the ONLY permitted fetch method for Screener Insights.
# The httpx fallback has been intentionally removed to prevent IP bans.
# Bulk httpx requests to Screener trigger CDN-level IP bans within minutes.
# Always start Chrome before running any Screener batch operation:
#   bash scripts/launch_chrome_debug.sh   (run from Terminal.app, NOT Windsurf)
# ──────────────────────────────────────────────────────────────────────────────


def _get_insights_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    insights_dir = data_root / symbol / "insights"
    insights_dir.mkdir(parents=True, exist_ok=True)
    return insights_dir


_NOISE_PHRASES = frozenset([
    "flag error",
    "log in to view insights",
    "login to view insights",
    "please log in to see hidden values",
    "log in",
    "login",
    "sign in",
    "register",
    "insights (beta)",
    "insights",
    "beta",
    "key insights",
])


def _is_noise(text: str) -> bool:
    """Return True if the text is a UI element, not an actual insight."""
    t = text.strip().lower()
    if not t:
        return True
    if t in _NOISE_PHRASES:
        return True
    # Login / flag prompts
    if t.startswith("log in") or t.startswith("please log in") or t.startswith("flag"):
        return True
    return False


def _sentiment_from_text(text: str, css_classes: str) -> str:
    cls = css_classes.lower()
    if any(kw in cls for kw in ("positive", "good", "up", "green", "success")):
        return "positive"
    if any(kw in cls for kw in ("negative", "bad", "down", "red", "danger", "warning")):
        return "negative"
    t = text.lower()
    if any(kw in t for kw in ("increas", "improv", "growth", "strong", "higher", "profit", "gain")):
        return "positive"
    if any(kw in t for kw in ("declin", "decreas", "lower", "loss", "weak", "fall", "reduc", "poor")):
        return "negative"
    return "neutral"


def _category_from_text(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in ("revenue", "sales", "turnover", "topline")):
        return "revenue"
    if any(kw in t for kw in ("profit", "margin", "ebitda", "pat", "eps", "net income", "pbit")):
        return "profitability"
    if any(kw in t for kw in ("debt", "borrowing", "leverage", "interest", "loan")):
        return "debt"
    if any(kw in t for kw in ("promoter", "holding", "insider", "shareholder")):
        return "shareholding"
    if any(kw in t for kw in ("roce", "roe", "return on", "roa", "roic")):
        return "return_ratios"
    if any(kw in t for kw in ("cash", "free cash", "fcf", "operating cash")):
        return "cash_flow"
    if any(kw in t for kw in ("order", "backlog", "pipeline", "book")):
        return "order_book"
    if any(kw in t for kw in ("dividend", "buyback", "payout", "yield")):
        return "capital_allocation"
    if any(kw in t for kw in ("pe", "p/e", "price to", "valuation", "book value")):
        return "valuation"
    if any(kw in t for kw in ("working capital", "inventory", "receivable", "payable")):
        return "working_capital"
    return "general"


def _extract_insights(soup: BeautifulSoup, symbol: str) -> list[dict]:
    """
    Extract insights from the Screener Insights table.

    Screener's DOM structure (id="insights"):
      <section id="insights">
        <div id="yearly-insights">
          <table class="data-table">
            <thead><tr><th/><th>Mar 2022</th>...</tr></thead>
            <tbody>
              <tr>
                <td class="text border-right">
                  Metric Label<br/><span>Unit · context</span>
                </td>
                <td> actual value OR blurred xx overlay </td>
                ...
              </tr>
            </tbody>
          </table>
        </div>
      </section>

    When NOT logged in:
      - The value cells contain a colspan/rowspan blurred overlay (aria-hidden table with "xx").
      - Metric labels are still visible — we capture those as label-only insights.
      - status note: "[values require Screener login]" appended.

    When logged in:
      - Each value <td> contains the real number.
      - We capture: "MetricLabel: most-recent-year = VALUE unit" as the insight text.

    Returns list of dicts: {text, sentiment, category}
    """
    insights: list[dict] = []

    section = soup.find(id="insights")
    if not section:
        return []

    # ── Check login state ─────────────────────────────────────────────────────
    # When not logged in, Screener renders a blurred overlay table with aria-hidden="true"
    # and a "Log in to view insights" prompt.
    blur_table = section.find("table", attrs={"aria-hidden": "true"})
    login_wall = section.find(string=lambda t: t and "Log in to view" in t)
    logged_in = (blur_table is None) and (login_wall is None)

    # ── Locate the data table (yearly tab is always present in DOM) ───────────
    yearly_div = section.find(id="yearly-insights") or section
    table = yearly_div.find("table", class_="data-table")
    if not table:
        return []

    # ── Parse column headers (years) ──────────────────────────────────────────
    headers: list[str] = []
    thead = table.find("thead")
    if thead:
        for th in thead.find_all("th")[1:]:  # skip first empty th
            headers.append(th.get_text(strip=True))

    # ── Parse metric rows ─────────────────────────────────────────────────────
    seen: set[str] = set()
    tbody = table.find("tbody")
    if not tbody:
        return []

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        # First cell = metric label + optional unit/context
        label_cell = cells[0]
        # Extract label text (ignore the unit span for now)
        label_parts = []
        for child in label_cell.children:
            if hasattr(child, "get_text"):
                t = child.get_text(strip=True)
            else:
                t = str(child).strip()
            if t and not _is_noise(t):
                label_parts.append(t)
        label = " ".join(label_parts).strip()
        if not label or _is_noise(label) or len(label) < 4:
            continue
        if label in seen:
            continue
        seen.add(label)

        if not logged_in:
            # No real values — record metric name with a note
            display = f"{label}  [values require Screener login]"
            insights.append({
                "text": display,
                "sentiment": "neutral",
                "category": _category_from_text(label),
            })
            continue

        # ── Logged in: extract actual values ──────────────────────────────────
        # Skip the colspan/rowspan login-wall cells; real cells have no colspan>1
        value_cells = [
            c for c in cells[1:]
            if not (c.get("colspan") and int(c.get("colspan", 1)) > 1)
        ]
        values: list[str] = []
        for vc in value_cells:
            v = vc.get_text(strip=True)
            if v and v not in ("", "-"):
                values.append(v)

        if not values:
            # Still nothing — just record the label
            display = label
        else:
            # Build "label: col1=v1, col2=v2 ..." using available headers
            pairs = []
            for i, v in enumerate(values):
                col = headers[i] if i < len(headers) else f"col{i+1}"
                pairs.append(f"{col}={v}")
            display = f"{label}: {', '.join(pairs)}"

        css_classes = " ".join(row.get("class", []))
        sentiment = _sentiment_from_text(label, css_classes)
        category = _category_from_text(label)
        insights.append({"text": display, "sentiment": sentiment, "category": category})

    return insights


def _format_insights_file(symbol: str, company_name: str, insights: list[dict],
                           screener_url: str, fetch_date: str) -> str:
    lines = [
        f"# Screener Insights — {symbol}",
        f"**Company:** {company_name}",
        f"**Source:** {screener_url}",
        f"**Fetched:** {fetch_date}",
        f"**Total insights:** {len(insights)}",
        "",
    ]

    # Group by category
    by_category: dict[str, list[dict]] = {}
    for ins in insights:
        cat = ins["category"]
        by_category.setdefault(cat, []).append(ins)

    sentiment_icon = {"positive": "✅", "negative": "⚠️", "neutral": "ℹ️"}

    for category, items in by_category.items():
        lines.append(f"## {category.replace('_', ' ').title()}")
        for item in items:
            icon = sentiment_icon.get(item["sentiment"], "ℹ️")
            lines.append(f"- {icon} {item['text']}")
        lines.append("")

    if not insights:
        lines.append("_No insights found on Screener for this company._")
        lines.append("")
        lines.append("Note: Screener Insights is a Beta feature and may not be available for all companies.")

    return "\n".join(lines)


def run(company: dict, config: dict, refresh: bool = False) -> dict:
    """
    Fetch Screener Insights for a company and save to insights/ folder.

    Args:
        company: Company dict with symbol, name, screener_url
        config:  Loaded config.yaml dict
        refresh: Force re-fetch even if today's file exists

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
        "data": {"saved": None, "insights_count": 0, "insights": []},
        "error": None,
    }

    if not screener_url:
        result["status"] = "error"
        result["error"] = "No screener_url configured for this company."
        print(f"[{SKILL_NAME}][{symbol}] No screener_url — cannot fetch.")
        return result

    try:
        insights_dir = _get_insights_dir(symbol)
        today = datetime.now().strftime("%Y%m%d")
        filename = f"{symbol}_insights_{today}.txt"
        dest_path = insights_dir / filename

        if dest_path.exists() and not refresh:
            print(f"[{SKILL_NAME}][{symbol}] Already fetched today — skipping (use refresh=True to force)")
            result["data"]["saved"] = str(dest_path)
            result["status"] = "skipped"
            # Load and return existing insights count
            content = dest_path.read_text(encoding="utf-8")
            lines = [l for l in content.splitlines() if l.startswith("- ")]
            result["data"]["insights_count"] = len(lines)
            return result

        print(f"[{SKILL_NAME}][{symbol}] Fetching insights from: {screener_url}")

        # Chrome CDP is the ONLY permitted fetch method — no httpx fallback.
        # httpx bulk requests to Screener cause CDN-level IP bans.
        if not _chrome_available():
            msg = (
                "Chrome CDP not available. "
                "Start Chrome first: bash scripts/launch_chrome_debug.sh  "
                "(run from Terminal.app, NOT the Windsurf terminal)"
            )
            result["status"] = "error"
            result["error"] = msg
            print(f"[{SKILL_NAME}][{symbol}] ABORTED — {msg}")
            return result

        html = _cdp_fetch(screener_url.rstrip("/"))
        if not html:
            result["status"] = "error"
            result["error"] = "Chrome CDP fetch returned empty HTML. Check Chrome is logged into Screener."
            print(f"[{SKILL_NAME}][{symbol}] Chrome CDP returned no HTML — check Chrome window.")
            return result

        soup = BeautifulSoup(html, "lxml")
        print(f"[{SKILL_NAME}][{symbol}] Fetched via Chrome CDP")

        # Always extract authoritative company name from Screener h1 and persist
        title_tag = soup.find("h1")
        if title_tag:
            scraped_name = title_tag.get_text(strip=True)
            if scraped_name and len(scraped_name) > 3:
                company_name = scraped_name
        store_company_meta(symbol, company_name, screener_url)

        insights = _extract_insights(soup, symbol)
        print(f"[{SKILL_NAME}][{symbol}] Found {len(insights)} insight(s)")

        # ── BSE-code URL fallback ───────────────────────────────────────────────
        # Some companies (BSE SME / BSE-only) have a numeric BSE code slug on
        # Screener (e.g. /company/541083/) rather than a symbol slug.
        # If the symbol-based URL returned 0 insights, resolve the actual URL.
        if not insights:
            _slug = [p for p in screener_url.rstrip("/").split("/") if p]
            _slug = _slug[-1] if _slug else ""
            if not _slug.isdigit():
                try:
                    from skills.financial_fetcher import _resolve_screener_company_url as _res_url
                    _clean = symbol.upper().replace("-SM", "").replace("-BE", "")
                    _resolved = _res_url(_clean)
                    if _resolved and _resolved.rstrip("/") != screener_url.rstrip("/"):
                        print(f"[{SKILL_NAME}][{symbol}] Retrying with BSE-code URL: {_resolved}")
                        _html2 = _cdp_fetch(_resolved.rstrip("/"))
                        if _html2:
                            _soup2 = BeautifulSoup(_html2, "lxml")
                            _ins2 = _extract_insights(_soup2, symbol)
                            if _ins2:
                                insights = _ins2
                                screener_url = _resolved
                                print(f"[{SKILL_NAME}][{symbol}] Found {len(insights)} insight(s) via BSE-code URL")
                except Exception as _exc:
                    print(f"[{SKILL_NAME}][{symbol}] BSE-code URL fallback failed: {_exc}")
        # ───────────────────────────────────────────────────────────────────────

        content = _format_insights_file(
            symbol, company_name, insights, screener_url, today
        )
        dest_path.write_text(content, encoding="utf-8")
        print(f"[{SKILL_NAME}][{symbol}] Saved: {dest_path}")

        result["data"]["saved"] = str(dest_path)
        result["data"]["insights_count"] = len(insights)
        result["data"]["insights"] = insights

        if not insights:
            result["status"] = "no_data"
            result["error"] = "Insights section not found or empty on Screener."

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")

    return result


if __name__ == "__main__":
    import sys
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.insights_fetcher <SYMBOL> [--refresh]")
        sys.exit(1)

    target_symbol = sys.argv[1].upper()
    force_refresh = "--refresh" in sys.argv

    # Try watchlist first, then portfolio_companies
    cfg = get_config()
    company_entry = None

    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        yaml_path = PROJECT_ROOT / yaml_file
        if not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        company_entry = next(
            (c for c in data.get("stocks", [])
             if c.get("symbol", "").upper() == target_symbol
             or str(c.get("screener_symbol", "")).upper() == target_symbol),
            None,
        )
        if company_entry:
            break

    if company_entry is None:
        print(f"[ERROR] Symbol '{target_symbol}' not found in watchlist.yaml or portfolio_companies.yaml.")
        sys.exit(1)

    fetch_result = run(company_entry, cfg, refresh=force_refresh)
    print(f"\nResult: {fetch_result['status']}")
    print(f"  Insights found: {fetch_result['data'].get('insights_count', 0)}")
    if fetch_result.get("data", {}).get("insights"):
        print("\nInsights preview:")
        for ins in fetch_result["data"]["insights"][:5]:
            print(f"  [{ins['sentiment']}] {ins['text'][:100]}")
