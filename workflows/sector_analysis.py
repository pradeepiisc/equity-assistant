"""
Workflow: Sector Analysis (Weekly)
====================================
For each meaningful holding (≥0.1% alloc), fetches a rich company profile
from Screener.in, uses LLM to extract:
  - What the company does (plain English)
  - Key products / services
  - Input sensitivity  (raw materials / cost drivers)
  - Output industries  (where their products are used / who the end customers are)

Profiles are cached per company in data/sector_profiles/{SYMBOL}.json
and expire after CACHE_DAYS days.  Only new/expired companies are re-fetched.

Report groups holdings by OUTPUT INDUSTRY so you can see cross-sector themes
(e.g. "AI / Data Centers" might include 4 companies from different 'sectors').

Usage:
    python -m workflows.sector_analysis                  # all meaningful holdings
    python -m workflows.sector_analysis --symbols AEROFLEX HFCL
    python -m workflows.sector_analysis --refresh        # ignore cache, re-fetch all
    python -m workflows.sector_analysis --no-llm         # scrape only, no LLM enrichment
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── Screener session (module-level) ───────────────────────────────────────────
# A persistent requests.Session carries cookies from the Screener homepage visit.
# Without prior homepage cookies, Screener tarpits automated requests (2-3 min+).
_SESSION: requests.Session | None = None


def _get_screener_session() -> requests.Session:
    """Return a warm Screener session (created once per process)."""
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        try:
            s.get("https://www.screener.in/", timeout=10)   # establish session cookies
        except Exception:
            pass
        _SESSION = s
    return _SESSION

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PORTFOLIO_DIR  = PROJECT_ROOT / "portfolio"
PROFILES_DIR   = PROJECT_ROOT / "data" / "sector_profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DAYS     = 30        # re-fetch company profile after this many days
MIN_ALLOC_PCT  = 0.1       # skip holdings below this % (MF/ETF slices)
SCREENER_SLEEP = 0.8       # seconds between Screener requests (session-based fallback)
CDP_URL        = "http://127.0.0.1:9222"  # Chrome debug port for CDP scraping

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}


# ── portfolio loading ──────────────────────────────────────────────────────────

def _find_user_dir(user_id: str | None = None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio data found. Run: python -m integrations.kite_connect --save-portfolio")
    if user_id:
        match = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
        if not match:
            raise FileNotFoundError(f"User '{user_id}' not found under portfolio/")
        return match
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_latest_holdings(user_dir: Path) -> tuple[list[dict], str]:
    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        raise FileNotFoundError(f"No holdings/ directory found under {user_dir}. Run: python -m integrations.kite_connect --save-portfolio")
    date_dirs = sorted([d for d in holdings_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    if not date_dirs:
        raise FileNotFoundError(f"No dated snapshots found under {holdings_dir}")
    latest = date_dirs[0]
    hf = latest / "holdings.json"
    if not hf.exists():
        raise FileNotFoundError(f"holdings.json missing in {latest}")
    with open(hf) as f:
        return json.load(f), latest.name


def _meaningful_holdings(holdings: list[dict]) -> list[dict]:
    total = sum(h["current_value"] for h in holdings)
    out = []
    for h in holdings:
        pct = h["current_value"] / total * 100 if total else 0
        if pct >= MIN_ALLOC_PCT:
            out.append({**h, "pct_of_portfolio": round(pct, 2)})
    return sorted(out, key=lambda x: -x["pct_of_portfolio"])


# ── cache ──────────────────────────────────────────────────────────────────────

def _cache_path(symbol: str) -> Path:
    return PROFILES_DIR / f"{symbol.upper().replace('-SM', '').replace(' ', '_')}.json"


def _load_cached(symbol: str) -> dict | None:
    p = _cache_path(symbol)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        fetched = datetime.fromisoformat(data.get("fetched_date", "2000-01-01"))
        if datetime.now() - fetched > timedelta(days=CACHE_DAYS):
            return None
        return data
    except Exception:
        return None


def _save_cache(profile: dict) -> None:
    p = _cache_path(profile["symbol"])
    profile["fetched_date"] = datetime.now().isoformat()
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2))


# ── Chrome CDP Screener scraper (primary — real browser, no tarpit) ───────────

def _scrape_screener_via_cdp(symbol: str, cdp_url: str = CDP_URL) -> dict | None:
    """
    Fetch Screener.in company page via Chrome CDP (real browser).
    Bypasses Screener bot detection/tarpit completely.
    Requires Chrome running with: bash scripts/launch_chrome_debug.sh
    """
    try:
        import websocket  # websocket-client  # noqa: PLC0415
    except ImportError:
        return None

    clean = symbol.upper().replace("-SM", "").replace("-BE", "")
    try:
        r = requests.get(f"{cdp_url}/json", timeout=3)
        targets = r.json()
        ws_url = next(
            (t["webSocketDebuggerUrl"] for t in targets if t.get("type") == "page"),
            None,
        )
        if not ws_url:
            return None

        ws = websocket.create_connection(ws_url, timeout=20)
        _cid = [0]

        def _send(method, params=None):
            _cid[0] += 1
            cid = _cid[0]
            ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            for _ in range(300):
                resp = json.loads(ws.recv())
                if resp.get("id") == cid:
                    return resp
            return {}

        nav_url = f"https://www.screener.in/company/{clean}/"
        _send("Page.navigate", {"url": nav_url})
        time.sleep(3)   # Screener is server-rendered; 3s is enough

        result = _send("Runtime.evaluate", {
            "expression": """
            (() => {
                // About text
                let about = '';
                const sec = document.querySelector('section#about') || document.querySelector('#about');
                if (sec) {
                    about = sec.innerText.replace(/\\s+/g, ' ').trim().slice(0, 2000);
                }
                if (!about) {
                    const meta = document.querySelector('meta[name="description"]');
                    if (meta) about = (meta.getAttribute('content') || '').slice(0, 1000);
                }
                // Industry tag
                let industry = '';
                const noise = new Set(['edit ratios','add notes','follow','watchlist','compare','export']);
                for (const sel of ['a[href*="/screen/"]', '.sub-text a', '.breadcrumb a']) {
                    for (const el of document.querySelectorAll(sel)) {
                        const t = (el.innerText || '').trim();
                        if (t.length > 3 && t.length < 80 && !t.startsWith('\u20b9') && !noise.has(t.toLowerCase())) {
                            industry = t; break;
                        }
                    }
                    if (industry) break;
                }
                // Company name
                const h1 = document.querySelector('h1');
                const name = h1 ? h1.innerText.trim() : '';
                // Validity check — real company page has ratios section
                const valid = !!document.querySelector('#top-ratios, .company-ratios');
                return JSON.stringify({about, industry, name, url: window.location.href, valid});
            })()
            """,
            "returnByValue": True,
        })
        ws.close()

        data = json.loads(result.get("result", {}).get("result", {}).get("value", "{}"))
        if not data.get("valid") or not data.get("name"):
            return None
        return {
            "name": data["name"],
            "about": data["about"],
            "industry_tag": data["industry"],
            "screener_url": data["url"],
        }
    except Exception:
        return None


# ── Screener scraping (session-based fallback) ─────────────────────────────────

def _screener_slug(symbol: str) -> str | None:
    """Return the Screener.in slug for a symbol.
    Tries the direct URL first (most NSE symbols match exactly), then falls back to search.
    """
    session = _get_screener_session()
    clean = symbol.upper().replace("-SM", "").replace("-BE", "")
    # Strategy 1: direct URL — fastest, works for most symbols
    for slug_candidate in [clean, clean.replace("&", ""), clean.replace(" ", "")]:
        try:
            direct = f"https://www.screener.in/company/{slug_candidate}/"
            r = session.get(direct, timeout=10)
            if r.status_code == 200 and "/company/" in r.url:
                # Resolved URL may differ (redirects) — use the final slug
                final_slug = [p for p in r.url.strip("/").split("/") if p]
                return final_slug[-1] if final_slug else slug_candidate
        except Exception:
            pass
    # Strategy 2: Screener search endpoint
    try:
        url = f"https://www.screener.in/search/?q={clean}"
        r = session.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        link = soup.select_one("ul.list-links a")
        if link:
            href = link.get("href", "")
            parts = [p for p in href.strip("/").split("/") if p]
            if len(parts) >= 2:
                return parts[1]
    except Exception:
        pass
    return None


def _scrape_screener(symbol: str) -> dict:
    """Fetch Screener.in company page. Tries Chrome CDP first, falls back to session."""
    # Primary: Chrome CDP (real browser, no Screener bot detection / tarpit)
    cdp_result = _scrape_screener_via_cdp(symbol)
    if cdp_result and cdp_result.get("about"):
        return cdp_result

    # Fallback: session-based requests with homepage cookie handshake
    slug = _screener_slug(symbol)
    if not slug:
        return {"name": symbol, "about": "", "industry_tag": "", "screener_url": ""}

    screener_url = f"https://www.screener.in/company/{slug}/"
    try:
        session = _get_screener_session()
        time.sleep(SCREENER_SLEEP)
        r = session.get(screener_url, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        # Company name
        name_tag = soup.select_one("h1")
        name = name_tag.get_text(strip=True) if name_tag else symbol

        # About section — try multiple selectors for Screener's HTML
        about = ""
        for sel in ["#about p", "div.about p", ".about-section p", ".company-description"]:
            paras = soup.select(sel)
            if paras:
                about = " ".join(p.get_text(" ", strip=True) for p in paras)[:2000]
                break
        if not about:
            # Fallback: grab any <section id='about'> text
            sec = soup.find("section", {"id": "about"}) or soup.find("div", {"id": "about"})
            if sec:
                about = " ".join(sec.get_text(" ", strip=True).split())[:2000]

        # Industry tag — Screener shows it as a badge/link near the company header
        industry_tag = ""
        # Try screener's company-industry badge first
        for sel in [".company-info .info-field", ".sub-text a", "a[href*='/screen/']", ".company-ratios .flex-row"]:
            for tag in soup.select(sel):
                text = tag.get_text(strip=True)
                _ui_noise = {"edit ratios", "add notes", "follow", "watchlist", "compare", "export"}
                if text and 3 < len(text) < 60 and not text.startswith("₹") and not text[0].isdigit() and text.lower() not in _ui_noise:
                    industry_tag = text
                    break
            if industry_tag:
                break

        # Fallback: try meta description for about
        if not about:
            meta = soup.find("meta", {"name": "description"})
            if meta:
                about = meta.get("content", "")[:1000]

        return {
            "name": name,
            "about": about,
            "industry_tag": industry_tag,
            "screener_url": screener_url,
        }
    except Exception as exc:
        return {"name": symbol, "about": "", "industry_tag": "", "screener_url": screener_url, "error": str(exc)}


# ── LLM enrichment ────────────────────────────────────────────────────────────

_PROMPT_FILE = PROJECT_ROOT / "prompts" / "sector_profile.txt"
_PROFILE_PROMPT_CACHE: str | None = None


def _load_profile_prompt() -> str:
    """Load sector_profile.txt from prompts/ (cached in memory)."""
    global _PROFILE_PROMPT_CACHE
    if _PROFILE_PROMPT_CACHE is None:
        _PROFILE_PROMPT_CACHE = _PROMPT_FILE.read_text(encoding="utf-8")
    return _PROFILE_PROMPT_CACHE


def _llm_enrich(symbol: str, scraped: dict) -> dict:
    """Call LLM to extract structured profile from scraped text."""
    try:
        from llm.utils import call_llm  # noqa: PLC0415
        template = _load_profile_prompt()
        prompt = template.format(
            symbol=symbol,
            name=scraped.get("name", symbol),
            industry_tag=scraped.get("industry_tag", ""),
            about=scraped.get("about", "")[:1500],
        )
        result = call_llm(prompt, expect_json=True)
        if isinstance(result, dict):
            return result
    except Exception as exc:
        print(f"    [LLM error for {symbol}]: {exc}")
    return {
        "business_description": scraped.get("about", "")[:300] or "(not available)",
        "key_products": [],
        "input_sensitivity": "(not enriched)",
        "output_industries": [scraped.get("industry_tag", "Unclassified")],
        "output_note": "",
    }


# ── profile builder ───────────────────────────────────────────────────────────

def fetch_profile(symbol: str, holding: dict, use_llm: bool = True, refresh: bool = False) -> dict:
    """Load from cache or scrape + enrich. Returns the full profile dict."""
    if not refresh:
        cached = _load_cached(symbol)
        if cached:
            return cached

    print(f"  [{symbol}] scraping Screener.in...", end=" ", flush=True)
    scraped = _scrape_screener(symbol)

    if use_llm:
        print("LLM...", end=" ", flush=True)
        enriched = _llm_enrich(symbol, scraped)
    else:
        enriched = {
            "business_description": scraped.get("about", "")[:300] or "(scrape only)",
            "key_products": [],
            "input_sensitivity": "(skipped — run without --no-llm)",
            "output_industries": [scraped.get("industry_tag", "Unclassified")] if scraped.get("industry_tag") else ["Unclassified"],
            "output_note": "",
        }

    profile = {
        "symbol": symbol,
        "name": scraped.get("name", symbol),
        "exchange": holding.get("exchange", "NSE"),
        "screener_url": scraped.get("screener_url", ""),
        "industry_tag": scraped.get("industry_tag", ""),
        **enriched,
    }
    _save_cache(profile)
    print("✓")
    return profile


# ── report builder ────────────────────────────────────────────────────────────

def build_sector_report(
    profiles: list[dict],
    holdings_map: dict[str, dict],
    snapshot_date: str,
    user_id: str,
) -> str:
    lines: list[str] = []

    total_covered_pct = sum(h["pct_of_portfolio"] for h in holdings_map.values())

    lines += [
        "# Sector Analysis Report",
        f"**{user_id}  ·  {snapshot_date}**\n",
        f"*{len(profiles)} companies profiled  ·  covers {total_covered_pct:.1f}% of portfolio*",
        f"*Cache: {CACHE_DAYS}-day TTL  ·  Run weekly or after major portfolio changes*\n",
        "---\n",
    ]

    # ── Section 1: Output Industry Groups ────────────────────────────────────
    lines.append("## Holdings by Output Industry (Where Products Are Used)\n")
    lines.append("*A company may appear under multiple themes.*\n")

    industry_map: dict[str, list[dict]] = {}
    for prof in profiles:
        h = holdings_map.get(prof["symbol"], {})
        for ind in prof.get("output_industries", ["Unclassified"]):
            if ind not in industry_map:
                industry_map[ind] = []
            industry_map[ind].append({**prof, "pct": h.get("pct_of_portfolio", 0), "pnl_pct": h.get("pnl_pct", 0)})

    # Sort industries by total allocation descending
    industry_totals = {ind: sum(c["pct"] for c in comps) for ind, comps in industry_map.items()}
    for ind in sorted(industry_map, key=lambda x: -industry_totals[x]):
        comps = sorted(industry_map[ind], key=lambda x: -x["pct"])
        total_pct = industry_totals[ind]
        lines.append(f"### {ind}  —  {total_pct:.1f}% combined portfolio exposure")
        lines.append("| Company | Alloc% | P&L% | Key Products / Role |")
        lines.append("|---|---|---|---|")
        for c in comps:
            kp = ", ".join(c.get("key_products", [])[:2]) or c.get("output_note", "—")
            sign = "+" if c["pnl_pct"] >= 0 else ""
            lines.append(f"| [{c['symbol']}]({c['screener_url']}) — {c['name']} | {c['pct']:.2f}% | {sign}{c['pnl_pct']:.1f}% | {kp[:80]} |")
        lines.append("")

    # ── Section 2: Input Cost Risk Groups ────────────────────────────────────
    lines.append("---\n")
    lines.append("## Input Cost Sensitivity\n")
    lines.append("*What macro factors affect each company's margins.*\n")
    lines.append("| Company | Alloc% | Input Sensitivity |")
    lines.append("|---|---|---|")
    for prof in sorted(profiles, key=lambda x: -holdings_map.get(x["symbol"], {}).get("pct_of_portfolio", 0)):
        h = holdings_map.get(prof["symbol"], {})
        pct = h.get("pct_of_portfolio", 0)
        sensitivity = prof.get("input_sensitivity", "—")[:100]
        lines.append(f"| [{prof['symbol']}]({prof['screener_url']}) | {pct:.2f}% | {sensitivity} |")
    lines.append("")

    # ── Section 3: Per-company profile cards ─────────────────────────────────
    lines.append("---\n")
    lines.append("## Company Profiles\n")
    for prof in sorted(profiles, key=lambda x: -holdings_map.get(x["symbol"], {}).get("pct_of_portfolio", 0)):
        h = holdings_map.get(prof["symbol"], {})
        pct = h.get("pct_of_portfolio", 0)
        pnl = h.get("pnl_pct", 0)
        sign = "+" if pnl >= 0 else ""
        lines.append(f"### {prof['symbol']} — {prof['name']}")
        lines.append(f"*Alloc: {pct:.2f}%  ·  P&L: {sign}{pnl:.1f}%  ·  [{prof['screener_url']}]({prof['screener_url']})*\n")
        lines.append(prof.get("business_description", ""))
        lines.append("")
        if prof.get("key_products"):
            lines.append(f"**Products:** {', '.join(prof['key_products'])}")
        if prof.get("output_note"):
            lines.append(f"**End use:** {prof['output_note']}")
        lines.append(f"**Output industries:** {', '.join(prof.get('output_industries', []))}")
        lines.append(f"**Input sensitivity:** {prof.get('input_sensitivity', '—')}")
        lines.append("")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(
    user_id: str | None = None,
    symbols: list[str] | None = None,
    use_llm: bool = True,
    refresh: bool = False,
) -> Path:
    user_dir = _find_user_dir(user_id)
    holdings_raw, snapshot_date = _load_latest_holdings(user_dir)
    all_holdings = _meaningful_holdings(holdings_raw)

    if symbols:
        syms_upper = {s.upper() for s in symbols}
        from_holdings = [h for h in all_holdings if h["symbol"].upper() in syms_upper]
        # Support watchlist / ad-hoc symbols not in portfolio — add as synthetic entries
        found_syms = {h["symbol"].upper() for h in from_holdings}
        extra = [
            {"symbol": s, "current_value": 0, "pnl_pct": 0, "pct_of_portfolio": 0, "exchange": "NSE"}
            for s in sorted(syms_upper - found_syms)
        ]
        if extra:
            print(f"  Note: {[e['symbol'] for e in extra]} not in holdings — profiling as watchlist stocks.")
        target = from_holdings + extra
        if not target:
            target = all_holdings
    else:
        target = all_holdings

    print(f"\n{'='*60}")
    print(f"  SECTOR ANALYSIS  —  {user_dir.name}  ·  snapshot {snapshot_date}")
    print(f"{'='*60}")
    print(f"  {len(target)} companies to profile  (cache TTL: {CACHE_DAYS} days)\n")

    profiles: list[dict] = []
    holdings_map: dict[str, dict] = {h["symbol"]: h for h in all_holdings}
    newly_fetched = 0

    for i, h in enumerate(target, 1):
        sym = h["symbol"]
        cached = not refresh and _load_cached(sym) is not None
        if cached:
            prof = _load_cached(sym)
            profiles.append(prof)
        else:
            newly_fetched += 1
            print(f"  [{i}/{len(target)}] ", end="")
            prof = fetch_profile(sym, h, use_llm=use_llm, refresh=refresh)
            profiles.append(prof)

    print(f"\n  {len(profiles)} profiles ready  ({newly_fetched} newly fetched, {len(profiles) - newly_fetched} from cache)")

    report_md = build_sector_report(profiles, holdings_map, snapshot_date, user_dir.name)

    out_file = user_dir / "sector" / snapshot_date / "sector_analysis.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(report_md, encoding="utf-8")
    print(f"✓ Report saved → {out_file}\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Weekly sector analysis — LLM-enriched company profiles")
    parser.add_argument("--user",       help="Kite user ID (auto-detected if only one)")
    parser.add_argument("--symbols",    nargs="+", metavar="SYM", help="Only process these symbols")
    parser.add_argument("--refresh",    action="store_true", help="Ignore cache, re-fetch everything")
    parser.add_argument("--no-llm",     action="store_true", help="Scrape only, skip LLM enrichment")
    args = parser.parse_args()

    run(
        user_id=args.user,
        symbols=args.symbols,
        use_llm=not args.no_llm,
        refresh=args.refresh,
    )
