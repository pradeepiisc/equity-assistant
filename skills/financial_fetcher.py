"""
Skill: Financial Fetcher
========================
Scrapes financial data from Screener.in for a company:
  - Key Ratios (current snapshot)
  - Profit & Loss (annual)
  - Quarterly Results
  - Balance Sheet
  - Cash Flow Statement
  - Historical Key Ratios

Tries the /consolidated/ URL first, falls back to standalone.
Saves as: data/companies/{SYMBOL}/financials/{SYMBOL}_financials_{YYYYMMDD}.txt

Used by: financial_snapshot.py skill for LLM analysis.
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from llm.client import get_config
from skills.cdp_helper import fetch_page_html as _cdp_fetch_html
from skills.cdp_helper import navigate_and_evaluate as _cdp_nav_eval

PROJECT_ROOT = Path(__file__).parent.parent

SKILL_NAME = "financial_fetcher"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
CDP_URL = "http://127.0.0.1:9222"

# ── Shared Screener session ────────────────────────────────────────────────────
_SCREENER_CLIENT: httpx.Client | None = None


def _get_screener_client() -> httpx.Client:
    global _SCREENER_CLIENT
    if _SCREENER_CLIENT is None:
        c = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    return _cdp_fetch_html(url)


def _fetch_financial_text_via_cdp(url: str) -> str | None:
    """Navigate Chrome to URL and extract financial tables via DOM (pipe-separated)."""
    ready_check = r"""
    (() => {
      const pl = document.querySelector('#profit-loss table');
      if (!pl) return false;
      const t = (pl.innerText || '');
      return /Mar\s*20\d{2}/.test(t) || /\bTTM\b/.test(t) || /\|\s*\d/.test(t);
    })()
    """
    expr = r"""
        (() => {
          const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

          const ratiosEl = document.querySelector('#top-ratios');
          const ratios = ratiosEl
            ? Array.from(ratiosEl.querySelectorAll('li')).map(li => clean(li.innerText)).filter(Boolean).join('\n')
            : '';

          const extractTable = (sec) => {
            if (!sec) return '';
            const table = sec.querySelector('table');
            if (!table) return '';
            const rows = Array.from(table.querySelectorAll('tr'))
              .map(tr => Array.from(tr.querySelectorAll('th,td')).map(td => clean(td.innerText)).join(' | '))
              .filter(r => r && r.replace(/\|/g,'').trim());
            return rows.join('\n');
          };

          const ids = ['profit-loss','quarters','balance-sheet','cash-flow','ratios'];
          const sections = {};
          for (const id of ids) {
            const sec = document.querySelector('#' + id);
            sections[id] = extractTable(sec);
          }
          const ok = Object.values(sections).some(v => v);
          return JSON.stringify({ratios, sections, ok});
        })()
        """

    payload = _cdp_nav_eval(
        url, expr,
        wait=2.0,
        ready_check_js=ready_check,
        ready_check_retries=20,
        ready_check_interval=1.0,
    )
    if not payload:
        return None
    data = json.loads(payload)
    if not data.get("ok"):
        return None

    parts: list[str] = []
    ratios = (data.get("ratios") or "").strip()
    if ratios:
        parts.append(f"## Key Ratios (Current)\n\n{ratios}")

    sections = data.get("sections") or {}
    for section_id, section_name in _SECTIONS:
        sec_txt = (sections.get(section_id) or "").strip()
        if sec_txt:
            parts.append(f"## {section_name}\n\n{sec_txt}")

    return "\n\n---\n\n".join(parts) if parts else None


def _resolve_company_url_via_search_api(query: str) -> str | None:
    q = (query or "").strip()
    if not q:
        return None
    try:
        client = _get_screener_client()
        r = client.get(
            "https://www.screener.in/api/company/search/",
            params={"q": q},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        href = (first.get("url") or first.get("link") or "").strip()
        if not href:
            return None
        if href.startswith("/company/"):
            return "https://www.screener.in" + href
        if href.startswith("http"):
            return href
    except Exception:
        return None
    return None


def _resolve_company_url_via_cdp(search_url: str) -> str | None:
    try:
        from websockets.sync.client import connect  # noqa: PLC0415
    except ImportError:
        return None
    try:
        targets = httpx.get(f"{CDP_URL}/json", timeout=3).json()
        ws_url = next((t["webSocketDebuggerUrl"] for t in targets if t.get("type") == "page"), None)
        if not ws_url:
            return None
        ws = connect(ws_url, open_timeout=20)
        _cid = [0]

        def _send(method, params=None):
            _cid[0] += 1
            cid = _cid[0]
            ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            for _ in range(300):
                raw = ws.recv(timeout=20)
                resp = json.loads(raw)
                if resp.get("id") == cid:
                    return resp
            return {}

        _send("Page.navigate", {"url": search_url})
        time.sleep(3)
        result = _send(
            "Runtime.evaluate",
            {
                "expression": """
                (() => {
                  const a = document.querySelector('ul.list-links a')
                         || document.querySelector('a[href^="/company/"]')
                         || document.querySelector('a[href*="/company/"]');
                  if (!a) return JSON.stringify({href: ''});
                  return JSON.stringify({href: a.getAttribute('href') || ''});
                })()
                """,
                "returnByValue": True,
            },
        )
        ws.close()
        payload = result.get("result", {}).get("result", {}).get("value", "")
        if not payload:
            return None
        data = json.loads(payload)
        href = (data.get("href") or "").strip()
        if not href:
            return None
        if href.startswith("/company/"):
            href = "https://www.screener.in" + href
        return href
    except Exception:
        return None


# ── Financial extraction helpers ───────────────────────────────────────────────

def _get_financials_dir(symbol: str) -> Path:
    config = get_config()
    data_root = PROJECT_ROOT / config["paths"]["data_root"]
    fin_dir = data_root / symbol / "financials"
    fin_dir.mkdir(parents=True, exist_ok=True)
    return fin_dir


def _extract_top_ratios(soup: BeautifulSoup) -> str:
    """Extract the quick key-ratios strip at the top of the page."""
    section = soup.find(id="top-ratios")
    if not section:
        return ""

    items: list[str] = []
    for li in section.find_all("li"):
        parts = [s.get_text(separator=" ", strip=True) for s in li.find_all("span")]
        if parts:
            items.append("  ".join(parts))
    return "\n".join(items)


def _resolve_screener_company_url(symbol: str, isin: str | None = None) -> str | None:
    query = (isin or symbol or "").strip()
    if not query:
        return None
    try:
        # Strategy 0: Screener's JSON search API (often works even when HTML is gated)
        for q in [isin, symbol, (symbol or "").upper().replace("-SM", "").replace("-BE", "")]:
            if not q:
                continue
            api_href = _resolve_company_url_via_search_api(str(q))
            if api_href:
                try:
                    u = httpx.URL(api_href)
                    path_parts = [p for p in u.path.split("/") if p]
                except Exception:
                    path_parts = [p for p in api_href.strip("/").split("/") if p]

                if "company" in path_parts:
                    idx = path_parts.index("company")
                    if idx + 1 < len(path_parts):
                        slug_or_code = path_parts[idx + 1]
                        return f"https://www.screener.in/company/{slug_or_code}/"

        client = _get_screener_client()
        search_url = "https://www.screener.in/search/"

        clean = (symbol or "").upper().replace("-SM", "").replace("-BE", "")

        html: str | None = None
        for params in [{"q": clean}, {"q": query}]:
            try:
                r = client.get(f"{search_url}?q={params['q']}", timeout=15)
                if r.status_code == 200 and r.text:
                    html = r.text
                    break
            except Exception:
                pass

        if not html:
            for params in [{"q": clean}, {"q": query}]:
                try:
                    r = client.get(search_url, params=params, timeout=15)
                    if r.status_code == 200 and r.text:
                        html = r.text
                        break
                except Exception:
                    pass

        if not html:
            for q in [clean, query]:
                if not q:
                    continue
                url = str(httpx.URL(search_url, params={"q": q}))
                resolved = _resolve_company_url_via_cdp(url)
                if resolved:
                    href = resolved
                    parts = [p for p in href.strip("/").split("/") if p]
                    if len(parts) >= 2 and parts[0] == "company":
                        slug_or_code = parts[1]
                        return f"https://www.screener.in/company/{slug_or_code}/"

                html = _fetch_page_via_cdp(url)
                if html:
                    break

        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        link = (
            soup.select_one("ul.list-links a")
            or soup.select_one("a[href^='/company/']")
            or soup.select_one("a[href*='/company/']")
        )
        href = (link.get("href") or "").strip() if link else ""

        if not href:
            m = re.search(r"href=['\"](?P<href>(?:https?://www\.screener\.in)?/company/[^'\"\s>]+/?)(?:['\"])", html)
            if m:
                href = m.group("href")

        if href.startswith("/company/"):
            href = "https://www.screener.in" + href

        parts = [p for p in href.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "company":
            slug_or_code = parts[1]
            return f"https://www.screener.in/company/{slug_or_code}/"
    except Exception:
        return None
    return None


def _extract_table_section(section) -> str:
    """
    Extract a Screener financial section's table as pipe-separated text rows.
    Handles both <table> and inline list-based layouts.
    """
    if section is None:
        return ""

    lines: list[str] = []
    table = section.find("table")
    if table:
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            vals = [c.get_text(separator=" ", strip=True) for c in cells]
            if any(v for v in vals):
                lines.append(" | ".join(vals))
    else:
        # Fallback: plain text of the section
        text = section.get_text(separator="\n", strip=True)
        lines = [ln for ln in text.splitlines() if ln.strip()]

    return "\n".join(lines)


_SECTIONS = [
    ("profit-loss",    "Annual Profit & Loss"),
    ("quarters",       "Quarterly Results"),
    ("balance-sheet",  "Balance Sheet"),
    ("cash-flow",      "Cash Flow Statement"),
    ("ratios",         "Historical Key Ratios"),
]


def _parse_financials(html: str) -> str:
    """Parse full page HTML and extract all financial sections as text."""
    soup = BeautifulSoup(html, "lxml")
    parts: list[str] = []

    # Current key ratios strip
    ratios_text = _extract_top_ratios(soup)
    if ratios_text:
        parts.append(f"## Key Ratios (Current)\n\n{ratios_text}")

    # Main financial tables
    for section_id, section_name in _SECTIONS:
        section = soup.find(id=section_id)
        if section is None:
            continue
        text = _extract_table_section(section)
        if text:
            parts.append(f"## {section_name}\n\n{text}")

    return "\n\n---\n\n".join(parts)


def _extract_section(fin_text: str, header: str) -> str:
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", fin_text, flags=re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    rest = fin_text[start:]
    m2 = re.search(r"^##\s+", rest, flags=re.MULTILINE)
    return rest[: m2.start()] if m2 else rest


def _looks_like_useful_financial_text(fin_text: str) -> bool:
    if not fin_text or not fin_text.strip():
        return False
    annual_pl = _extract_section(fin_text, "Annual Profit & Loss")
    if not annual_pl.strip():
        return False
    if re.search(r"\|\s*Mar\s*\d{4}", annual_pl):
        return True
    if re.search(r"^Sales \+\s*\|.*\d", annual_pl, flags=re.MULTILINE):
        return True
    return False


def _looks_like_financial_page(html: str) -> bool:
    h = (html or "").lower()
    if not h:
        return False
    markers = [
        "top-ratios",
        "id=\"profit-loss\"",
        "id=\"quarters\"",
        "id=\"balance-sheet\"",
        "id=\"cash-flow\"",
        "id=\"ratios\"",
    ]
    return any(m in h for m in markers)


def _fetch_financials(screener_url: str) -> str:
    """
    Fetch financial page HTML from Screener.
    Tries consolidated first, then standalone.
    Returns extracted financial text or empty string on failure.
    """
    base = screener_url.rstrip("/")
    base = re.sub(r"/(consolidated|standalone)$", "", base)
    urls_to_try = [f"{base}/consolidated/", f"{base}/standalone/", f"{base}/"]

    for url in urls_to_try:
        # Primary: Chrome CDP
        text = _fetch_financial_text_via_cdp(url)
        if text and _looks_like_useful_financial_text(text):
            print(f"    [{SKILL_NAME}] Using CDP(DOM) → {url}")
            return text

        html = _fetch_page_via_cdp(url)
        if html and _looks_like_financial_page(html):
            print(f"    [{SKILL_NAME}] Using CDP → {url}")
            parsed = _parse_financials(html)
            if _looks_like_useful_financial_text(parsed):
                return parsed

    return ""


# ── Public run() interface ─────────────────────────────────────────────────────

def run(company: dict, config: dict, refresh: bool = False) -> dict:
    """
    Fetch and save Screener financial data for a company.

    Args:
        company:  Dict with at least 'symbol' and 'screener_url'.
        config:   Loaded config.yaml dict.
        refresh:  If True, re-fetch even if a file already exists for today.

    Returns:
        Standard skill result dict: skill_name, symbol, status, data, error.
    """
    symbol = company.get("symbol", "").upper()
    screener_url = company.get("screener_url", "").strip()
    isin = (company.get("isin") or "").strip() or None

    result: dict = {
        "skill_name": SKILL_NAME,
        "symbol": symbol,
        "status": "success",
        "data": {"saved": [], "skipped": []},
        "error": None,
    }

    if not screener_url:
        resolved = _resolve_screener_company_url(symbol, isin=isin)
        if resolved:
            screener_url = resolved
            result["data"]["resolved_url"] = resolved
        else:
            result["status"] = "no_data"
            result["error"] = "No screener_url in company config and could not resolve via Screener search."
            print(f"[{SKILL_NAME}][{symbol}] No screener_url and could not resolve — skipping.")
            return result

    try:
        fin_dir = _get_financials_dir(symbol)
        today = datetime.now().strftime("%Y%m%d")
        out_file = fin_dir / f"{symbol}_financials_{today}.txt"

        if out_file.exists() and not refresh:
            print(f"[{SKILL_NAME}][{symbol}] Already fetched today — using cached file.")
            result["data"]["skipped"].append(str(out_file))
            return result

        print(f"[{SKILL_NAME}][{symbol}] Fetching financials from Screener...")
        text = _fetch_financials(screener_url)

        if not text.strip():
            resolved = _resolve_screener_company_url(symbol, isin=isin)
            if resolved and resolved.rstrip("/") != screener_url.rstrip("/"):
                screener_url = resolved
                result["data"]["resolved_url"] = resolved
                text = _fetch_financials(screener_url)

        if not text.strip():
            result["status"] = "no_data"
            result["error"] = "Could not extract financial data from Screener (page may be gated or symbol unknown)."
            print(f"[{SKILL_NAME}][{symbol}] No financial data extracted.")
            return result

        out_file.write_text(text, encoding="utf-8")
        print(f"[{SKILL_NAME}][{symbol}] Saved: {out_file}")
        result["data"]["saved"].append(str(out_file))

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"[{SKILL_NAME}][{symbol}] ERROR: {exc}")

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m skills.financial_fetcher <SYMBOL> [--refresh]")
        print("Example: python -m skills.financial_fetcher ZENTEC")
        sys.exit(1)

    sym = sys.argv[1].upper()
    force_refresh = "--refresh" in sys.argv
    url_override: str | None = None
    if "--screener-url" in sys.argv:
        try:
            url_override = sys.argv[sys.argv.index("--screener-url") + 1]
        except Exception:
            url_override = None

    wl_path = PROJECT_ROOT / "watchlist.yaml"
    with open(wl_path) as f:
        wl = yaml.safe_load(f)

    company = next(
        (c for c in wl.get("stocks", []) if c.get("symbol", "").upper() == sym),
        {"symbol": sym, "screener_url": f"https://www.screener.in/company/{sym}/"},
    )
    if url_override:
        company = {**company, "symbol": sym, "screener_url": url_override.strip()}

    cfg = get_config()
    r = run(company, cfg, refresh=force_refresh)
    print(f"\nStatus: {r['status']}")
    if r.get("error"):
        print(f"Error:  {r['error']}")
