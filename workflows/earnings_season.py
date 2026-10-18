"""
Workflow: Earnings Season
==========================
Weekly / daily workflow during quarterly results season (Jan-Feb, Apr-May,
Jul-Aug, Oct-Nov).  Detects changes since the last run and produces a
**delta-only** report for decision making.

What it covers:
  1. New quarterly results / financial data detected
  2. New concall transcripts available
  3. Shareholding pattern changes (promoter / FII / DII shifts)
  4. DMA40 + DMA100 trend classification — bullish / bearish / neutral
  5. Portfolio allocation by company and sector
  6. SME vs normal company flag
  7. Deployment candidates (bullish + under-allocated)
  8. Caution list (bearish + significant allocation)

Usage:
    python -m workflows.earnings_season                         # delta report
    python -m workflows.earnings_season --user ZV3899
    python -m workflows.earnings_season --fetch                 # also fetch transcripts (no CDP)
    python -m workflows.earnings_season --fetch-all             # full fetch incl. CDP data
    python -m workflows.earnings_season --full-refresh          # ignore previous state
    python -m workflows.earnings_season --symbols QPOWER BETA

Cron-friendly: without --fetch/--fetch-all, runs entirely offline
(yfinance for DMA, local file scan for deltas).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"
COMPANIES_DIR = PROJECT_ROOT / "data" / "companies"
CACHE_DIR = PORTFOLIO_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = CACHE_DIR / "earnings_season_state.json"
EARNINGS_SEASON_DIR = PROJECT_ROOT / "data" / "earnings_season"

# Subfolders to track for changes
TRACKED_DIRS = ["transcripts", "ppt", "financials", "shareholding", "insights"]

# SME suffixes in trading symbols
SME_SUFFIXES = ("-SM", "-BE", "-SME")

# ETF symbols — skip
ETF_SYMBOLS = {"GOLDIETF", "SILVERIETF", "NIFTYBEES", "JUNIORBEES", "LIQUIDBEES"}

# Allocation threshold: below this = "under-allocated" (deployment candidate)
DEPLOY_ALLOC_THRESH = 2.0

# Allocation threshold: above this = "significant" (flag on caution list)
CAUTION_ALLOC_THRESH = 0.5


# ── Screener.in CDP scraper (Chrome DevTools Protocol) ──────────────────────
#
# CRITICAL: Screener batch operations MUST use Chrome CDP (real browser session).
# Bulk httpx requests trigger IP bans from Screener's CDN within minutes.
# See .windsurf/screener_cdp_notes.md for full architecture & recovery guide.

from skills.cdp_helper import fetch_page_html as _cdp_fetch_page
from skills.cdp_helper import fetch_pages_parallel as _cdp_fetch_parallel
from skills.cdp_helper import ensure_chrome_running as _ensure_chrome
from skills.cdp_helper import is_available as _cdp_is_available

FETCH_DELAY_MIN = 3.0   # jittered delay range between Screener page loads (CDP)
FETCH_DELAY_MAX = 6.0
AJAX_DELAY = 0.3         # short delay between lightweight AJAX calls


def _screener_urls(symbol: str, screener_url: str = "") -> list[str]:
    """Return candidate Screener URLs for a company."""
    if screener_url:
        base = screener_url.rstrip("/")
        return [base + "/", base.replace("/consolidated/", "/") + "/"]
    slug = re.sub(r"-SM$", "", symbol, flags=re.IGNORECASE)
    return [
        f"https://www.screener.in/company/{slug}/",
        f"https://www.screener.in/company/{slug}/consolidated/",
    ]


def _looks_like_block(error_str: str) -> bool:
    """Check if an error string looks like an IP ban / CDN block."""
    markers = ["SSL", "EOF", "handshake", "Connection refused", "ConnectError", "403"]
    return any(m.lower() in error_str.lower() for m in markers)


def _parse_shareholding_html(html: str, symbol: str, fetch_holders: bool = True) -> dict | None:
    """Parse shareholding data from Screener HTML. Returns parsed data or None.
    Separated from fetch to allow parallel CDP batch fetching."""
    try:
        soup = BeautifulSoup(html, "lxml")
        section = soup.find("section", id="shareholding")
        if not section:
            return None
        table = section.find("table")
        if not table:
            return None

        # Parse quarters from header
        thead = table.find("thead")
        quarters: list[str] = []
        if thead:
            ths = [th.get_text(strip=True) for th in thead.find_all("th")]
            quarters = [q for q in ths[1:] if q]

        # Parse rows
        categories: dict[str, list[float | None]] = {}
        raw_lines: list[str] = []
        if quarters:
            raw_lines.append("| Category | " + " | ".join(quarters) + " |")
            raw_lines.append("|" + "---|" * (len(quarters) + 1))
        tbody = table.find("tbody")
        if tbody:
            for tr in tbody.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if not cells:
                    continue
                cat = cells[0].get_text(strip=True)
                vals_raw = [c.get_text(strip=True) for c in cells[1:]]
                vals: list[float | None] = []
                for v in vals_raw:
                    try:
                        vals.append(float(v.replace("%", "").replace(",", "").strip()))
                    except ValueError:
                        vals.append(None)
                categories[cat] = vals
                raw_lines.append(f"| {cat} | " + " | ".join(vals_raw) + " |")

        # Get company_id for individual holders
        company_id = None
        tag = soup.find(attrs={"data-url": re.compile(r"/trades/company-\d+/")})
        if tag:
            m = re.search(r"/trades/company-(\d+)/", str(tag.get("data-url", "")))
            if m:
                company_id = m.group(1)

        # Fetch individual holders (lightweight AJAX — httpx is OK here)
        individual: dict[str, list[dict]] = {}
        if company_id and fetch_holders:
            individual = _fetch_individual_holders(company_id, quarters)

        # Extract company name from h1
        h1 = soup.find("h1")
        company_name = h1.get_text(strip=True) if h1 else symbol

        return {
            "quarters": quarters,
            "categories": categories,
            "raw_text": "\n".join(raw_lines),
            "individual": individual,
            "company_name": company_name,
            "company_id": company_id,
        }
    except Exception:
        return None


def _fetch_screener_shareholding(symbol: str, screener_url: str = "") -> dict | None:
    """Fetch shareholding from Screener via Chrome CDP. Returns parsed data or None."""
    for url in _screener_urls(symbol, screener_url):
        html = _cdp_fetch_page(url)
        if not html:
            continue
        result = _parse_shareholding_html(html, symbol)
        if result:
            return result
    return None


def _fetch_individual_holders(company_id: str, quarters: list[str]) -> dict[str, list[dict]]:
    """Fetch individual holder data from Screener AJAX API.
    Uses httpx for these lightweight JSON calls (established pattern from shareholding_fetcher.py)."""
    classifications = {
        "foreign_institutions": "FIIs (Individual)",
        "domestic_institutions": "DIIs (Individual)",
        "public": "Public Anchor Investors",
        "promoters": "Promoters (Individual)",
    }
    results: dict[str, list[dict]] = {}
    ajax_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    for cls, label in classifications.items():
        url = f"https://www.screener.in/api/3/{company_id}/investors/{cls}/quarterly/"
        try:
            resp = httpx.get(url, headers=ajax_headers, timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                continue
            raw = resp.json()
            investors = []
            for name, data in raw.items():
                if not isinstance(data, dict):
                    continue
                holder_vals = {q: data[q] for q in quarters if q in data}
                if holder_vals:
                    investors.append({"name": name, "values": holder_vals})
            if investors:
                results[label] = investors
        except Exception:
            pass
        time.sleep(AJAX_DELAY)
    return results


def _load_tracked_investors() -> list[str]:
    """Load all tracked investor names from investor_watchlist.yaml."""
    wl_path = PROJECT_ROOT / "investor_watchlist.yaml"
    if not wl_path.exists():
        return []
    data = yaml.safe_load(wl_path.read_text()) or {}
    tracked = data.get("tracked_investors", data)
    names: list[str] = []
    seen: set[str] = set()
    for group in tracked.values():
        if isinstance(group, list):
            for n in group:
                if n not in seen:
                    names.append(n)
                    seen.add(n)
    return names


def _normalize_investor_name(name: str) -> str:
    """Normalize investor name for fuzzy matching.
    Handles: Pvt/Private, Ltd/Limited, Ins/Institute, camelCase, punctuation, extra whitespace."""
    # Split camelCase before lowering (e.g., "KotakInfrastructure" → "Kotak Infrastructure")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = s.lower().strip()
    s = re.sub(r"\bpvt\b\.?", "private", s)
    s = re.sub(r"\bltd\b\.?", "limited", s)
    s = re.sub(r"\bco\b\.?", "company", s)
    s = re.sub(r"\bcorp\b\.?", "corporation", s)
    s = re.sub(r"\bins\b\.?", "institute", s)
    s = re.sub(r"\btechno\b", "technology", s)
    s = re.sub(r"['\"\\.\-,&\(\)]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _detect_tracked_investor_entries(
    individual: dict[str, list[dict]],
    tracked_names: list[str],
    quarters: list[str],
) -> list[dict]:
    """Find tracked investors in individual holder data. Returns alerts."""
    alerts: list[dict] = []
    if not quarters or not tracked_names:
        return alerts
    # Pre-normalize tracked names
    norm_map = {_normalize_investor_name(t): t for t in tracked_names}
    latest_q = quarters[-1]
    prev_q = quarters[-2] if len(quarters) >= 2 else ""
    for label, investors in individual.items():
        for inv in investors:
            inv_name = inv["name"]
            inv_norm = _normalize_investor_name(inv_name)
            # Substring match first, then fuzzy fallback for spelling variants
            matched = next((orig for norm, orig in norm_map.items() if norm in inv_norm or inv_norm in norm), None)
            if not matched:
                from difflib import SequenceMatcher
                # Require 0.92+ ratio AND first word overlap to avoid false positives
                # (e.g. "Nalanda India Fund" vs "Malabar India Fund" = 0.885 → reject)
                inv_first = inv_norm.split()[0] if inv_norm.split() else ""
                for norm, orig in norm_map.items():
                    if SequenceMatcher(None, inv_norm, norm).ratio() >= 0.92:
                        matched = orig
                        break
                    # Also accept 0.85+ if the first word matches (genuine spelling variants)
                    norm_first = norm.split()[0] if norm.split() else ""
                    if norm_first and inv_first == norm_first and SequenceMatcher(None, inv_norm, norm).ratio() >= 0.85:
                        matched = orig
                        break
            if not matched:
                continue
            latest_val = inv["values"].get(latest_q, 0)
            prev_val = inv["values"].get(prev_q, 0) if prev_q else 0
            try:
                lv = float(latest_val) if latest_val else 0
                pv = float(prev_val) if prev_val else 0
            except (ValueError, TypeError):
                lv, pv = 0, 0
            if lv > 0 or pv > 0:
                if lv == 0 and pv > 0:
                    trend = "EXIT"
                elif pv == 0 and lv > 0:
                    trend = "NEW"
                elif lv > pv + 0.01:
                    trend = "UP"
                elif lv < pv - 0.01:
                    trend = "DOWN"
                else:
                    trend = "STABLE"
                alerts.append({
                    "investor": inv_name,
                    "matched_name": matched,
                    "category": label,
                    "latest_pct": lv,
                    "prev_pct": pv,
                    "trend": trend,
                    "latest_q": latest_q,
                    "prev_q": prev_q,
                })
    return alerts


# ── Shareholding file parser ─────────────────────────────────────────────

def _parse_existing_shareholding(symbol: str) -> dict | None:
    """Parse the latest existing shareholding .txt file for a company.
    Returns {quarters, categories: {name: [values]}, latest_quarter} or None."""
    sh_dir = COMPANIES_DIR / symbol.upper() / "shareholding"
    if not sh_dir.exists():
        return None
    files = sorted(sh_dir.glob(f"{symbol.upper()}_shareholding_*.txt"), reverse=True)
    if not files:
        return None
    text = files[0].read_text(encoding="utf-8")
    # Extract quarters
    qm = re.search(r"## Quarters Covered\s*\n(.+)", text)
    if not qm:
        return None
    quarters = [q.strip() for q in qm.group(1).split(",")]
    # Parse table rows
    categories: dict[str, list[float | None]] = {}
    for m in re.finditer(r"^\| (Promoters\+|FIIs\+|DIIs\+|Public\+|No\. of Shareholders) \|(.+)$", text, re.MULTILINE):
        cat = m.group(1)
        vals_raw = [v.strip() for v in m.group(2).rstrip("|").split("|")]
        vals: list[float | None] = []
        for v in vals_raw:
            try:
                vals.append(float(v.replace("%", "").replace(",", "").strip()))
            except ValueError:
                vals.append(None)
        categories[cat] = vals

    # Parse Individual Holder Detail tables (for tracked investor re-detection on skip)
    individual: dict[str, list[dict]] = {}
    section_re = re.compile(r"^### (.+)$", re.MULTILINE)
    row_re = re.compile(r"^\| (.+?) \| (.+?) \| (.+?) \| .+? \|$", re.MULTILINE)
    section_matches = list(section_re.finditer(text))
    for i, sm in enumerate(section_matches):
        label = sm.group(1).strip()
        end = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(text)
        block = text[sm.end():end]
        # Determine the quarter columns from header line (handles 1- or 2-quarter headers)
        hdr_line = re.search(r"^\| Investor \|.+\|$", block, re.MULTILINE)
        if not hdr_line:
            continue
        hdr_cols = [c.strip() for c in hdr_line.group(0).split("|")]
        # Filter to only quarter-like columns (exclude empty, 'Investor', 'Trend', '---')
        q_cols = [c for c in hdr_cols if c and c not in ("Investor", "Trend", "---")]
        if not q_cols:
            continue
        if len(q_cols) >= 2:
            prev_q, latest_q = q_cols[-2], q_cols[-1]
        else:
            prev_q, latest_q = "", q_cols[0]
        investors = []
        for rm in row_re.finditer(block):
            name, pv_raw, lv_raw = rm.group(1).strip(), rm.group(2).strip(), rm.group(3).strip()
            if name in ("Investor", "---"):
                continue
            values: dict[str, str] = {}
            if prev_q:
                values[prev_q] = pv_raw.rstrip("%")
            if latest_q:
                values[latest_q] = lv_raw.rstrip("%")
            investors.append({"name": name, "values": values})
        if investors:
            individual[label] = investors

    return {"quarters": quarters, "categories": categories, "individual": individual, "file": str(files[0])}


def _save_shareholding_file(
    symbol: str,
    company_name: str,
    data: dict,
    tracked_alerts: list[dict],
) -> Path:
    """Save shareholding data to data/companies/{SYMBOL}/shareholding/ in standard format.
    Deletes all previous files first — one file per company (sliding window via Screener)."""
    sh_dir = COMPANIES_DIR / symbol.upper() / "shareholding"
    sh_dir.mkdir(parents=True, exist_ok=True)
    # Delete existing files (sliding window: Screener already drops oldest quarter)
    for old_file in sh_dir.glob(f"{symbol.upper()}_shareholding_*.txt"):
        old_file.unlink(missing_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    filename = f"{symbol.upper()}_shareholding_{today_str}.txt"
    dest = sh_dir / filename

    quarters = data.get("quarters", [])
    individual = data.get("individual", {})
    lines = [
        f"# Shareholding Pattern — {company_name} ({symbol})",
        f"*Fetched: {datetime.now().strftime('%d %b %Y')} | Source: Screener.in*\n",
    ]
    if quarters:
        lines.append(f"## Quarters Covered\n{', '.join(quarters)}\n")
    lines.append("## Full Shareholding Table\n")
    lines.append(data.get("raw_text", "No data"))
    lines.append("")

    # Key holdings summary
    cats = data.get("categories", {})
    key_cats = ["Promoters+", "FIIs+", "DIIs+", "Public+"]
    if quarters:
        lines.append("## Key Holdings Summary (Most Recent Quarter)\n")
        lines.append(f"*Quarter: {quarters[-1]}*\n")
        for cat in key_cats:
            vals = cats.get(cat, [])
            latest = vals[-1] if vals else None
            lines.append(f"- **{cat}**: {latest:.2f}%" if latest is not None else f"- **{cat}**: —")
        lines.append("")

    # Individual holder detail
    if individual:
        latest_q = quarters[-1] if quarters else ""
        prev_q = quarters[-2] if len(quarters) > 1 else ""
        lines.append("## Individual Holder Detail\n")
        for label, investors in individual.items():
            lines.append(f"### {label}")
            lines.append(f"| Investor | {prev_q} | {latest_q} | Trend |")
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

    # Tracked investor alerts
    if tracked_alerts:
        lines.append("## Tracked Investor Alerts\n")
        for a in tracked_alerts:
            icon = {"NEW": "🆕", "EXIT": "🚪", "UP": "⬆", "DOWN": "⬇", "STABLE": "→"}.get(a["trend"], "?")
            lines.append(
                f"- {icon} **{a['investor']}** — "
                f"latest: {a['latest_pct']:.2f}%  prev: {a['prev_pct']:.2f}%  trend: {a['trend']}"
            )
        lines.append("")

    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def _compare_shareholding(
    old_data: dict | None,
    new_data: dict | None,
) -> dict | None:
    """Compare old vs new shareholding. Returns change summary or None if no meaningful change."""
    if not new_data:
        return None
    new_quarters = new_data.get("quarters", [])
    new_cats = new_data.get("categories", {})
    if not new_quarters:
        return None

    # Normalize category names: Screener uses "Promoters+" in raw text,
    # existing files also use "Promoters+"
    KEY_CATS = ["Promoters+", "FIIs+", "DIIs+", "Public+"]

    if old_data is None:
        # First time: return current state, flag as "baseline"
        latest: dict[str, float | None] = {}
        for cat in KEY_CATS:
            vals = new_cats.get(cat, [])
            latest[cat] = vals[-1] if vals else None
        return {
            "type": "baseline",
            "latest_quarter": new_quarters[-1],
            "latest": latest,
            "changes": {},
        }

    old_quarters = old_data.get("quarters", [])
    old_cats = old_data.get("categories", {})

    # Check if new quarter appeared
    new_quarter_added = new_quarters[-1] if new_quarters[-1] not in old_quarters else None

    # Compare latest quarter values
    changes: dict[str, dict] = {}
    for cat in KEY_CATS:
        old_vals = old_cats.get(cat, [])
        new_vals = new_cats.get(cat, [])
        old_latest = old_vals[-1] if old_vals else None
        new_latest = new_vals[-1] if new_vals else None
        if old_latest is not None and new_latest is not None:
            delta = new_latest - old_latest
            if abs(delta) > 0.01:
                changes[cat] = {"old": old_latest, "new": new_latest, "delta": delta}
        elif new_latest is not None and old_latest is None:
            changes[cat] = {"old": None, "new": new_latest, "delta": None}

    if not changes and not new_quarter_added:
        return None  # No meaningful change

    latest: dict[str, float | None] = {}
    for cat in KEY_CATS:
        vals = new_cats.get(cat, [])
        latest[cat] = vals[-1] if vals else None

    return {
        "type": "new_quarter" if new_quarter_added else "updated",
        "new_quarter": new_quarter_added,
        "old_quarter": old_quarters[-1] if old_quarters else None,
        "latest_quarter": new_quarters[-1],
        "latest": latest,
        "changes": changes,
    }


def _expected_latest_quarter() -> str | None:
    """Estimate the latest quarter whose shareholding data would be available today."""
    today = date.today()
    y = today.year
    m = today.month
    # Q4 (Jan-Mar): available from ~Apr; Q3 (Oct-Dec): available from ~Feb
    # Q2 (Jul-Sep): available from ~Oct; Q1 (Apr-Jun): available from ~Jul
    if m >= 10:         return f"Sep {y}"
    if m >= 7:          return f"Jun {y}"
    if m >= 4:          return f"Mar {y}"
    return f"Dec {y-1}"


# ── Batch shareholding fetch ─────────────────────────────────────────────

def _fetch_shareholding_batch(
    companies: list[dict],
    tracked_names: list[str],
    force: bool = False,
) -> dict[str, dict]:
    """Fetch fresh shareholding for companies via Chrome CDP (real browser).
    Uses parallel CDP tabs (3 concurrent) to cut wall time by ~3×.
    Skips companies whose existing file already has the expected current quarter.
    Returns {symbol: {old, new, comparison, tracked_alerts, saved_path}}."""
    results: dict[str, dict] = {}
    expected_q = _expected_latest_quarter()

    # Pre-flight: ensure Chrome is running with CDP debug port
    if not _ensure_chrome(verbose=True):
        print("  ✗ Chrome CDP not available — cannot fetch shareholding.")
        print("    Launch Chrome: bash scripts/launch_chrome_debug.sh  (from Terminal.app)")
        return results

    # Phase A: filter out companies already at current quarter
    to_fetch: list[dict] = []  # companies that need CDP fetch
    for company in companies:
        sym = company.get("symbol", "").upper()
        old_data = _parse_existing_shareholding(sym)

        if not force and old_data and expected_q:
            existing_latest = old_data.get("quarters", [""])[-1] if old_data.get("quarters") else ""
            if existing_latest == expected_q:
                tracked_alerts = _detect_tracked_investor_entries(
                    old_data.get("individual", {}), tracked_names, old_data.get("quarters", [])
                )
                alert_str = f"  🔔 {len(tracked_alerts)} tracked investors" if tracked_alerts else ""
                print(f"  {sym:<16} ⏭  already has {expected_q} — skipped{alert_str}")
                results[sym] = {
                    "old": old_data, "new": None, "comparison": None,
                    "tracked_alerts": tracked_alerts, "saved_path": None, "skipped": True,
                }
                continue
        to_fetch.append(company)

    if not to_fetch:
        return results

    print(f"  Fetching {len(to_fetch)} companies via parallel CDP (3 tabs)...")

    # Phase B: build primary URLs for all companies
    url_to_sym: dict[str, str] = {}
    sym_to_urls: dict[str, list[str]] = {}
    primary_urls: list[str] = []

    for company in to_fetch:
        sym = company.get("symbol", "").upper()
        urls = _screener_urls(sym, company.get("screener_url", ""))
        sym_to_urls[sym] = urls
        if urls:
            primary_urls.append(urls[0])
            url_to_sym[urls[0]] = sym

    # Phase C: parallel fetch primary URLs
    html_map = _cdp_fetch_parallel(primary_urls)

    # Phase D: retry with alternate URLs for failures
    retry_urls: list[str] = []
    for sym, urls in sym_to_urls.items():
        primary = urls[0] if urls else ""
        html = html_map.get(primary)
        # Check if HTML has shareholding section (quick sanity check)
        if html and "id=\"shareholding\"" in html:
            continue  # primary URL worked
        if len(urls) > 1:
            retry_urls.append(urls[1])
            url_to_sym[urls[1]] = sym

    if retry_urls:
        print(f"  Retrying {len(retry_urls)} companies with alternate URL...")
        retry_map = _cdp_fetch_parallel(retry_urls)
        html_map.update(retry_map)

    # Phase E: parse all HTML, fetch AJAX holders, save files
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 10  # higher threshold since parallel is faster

    for i, company in enumerate(to_fetch, 1):
        sym = company.get("symbol", "").upper()
        old_data = _parse_existing_shareholding(sym)
        urls = sym_to_urls.get(sym, [])

        # Find the best HTML from primary or alternate URL
        new_data = None
        for url in urls:
            html = html_map.get(url)
            if html:
                parsed = _parse_shareholding_html(html, sym, fetch_holders=True)
                if parsed:
                    new_data = parsed
                    break

        print(f"  [{i:>3}/{len(to_fetch)}] {sym:<16}", end="", flush=True)

        if not new_data:
            consecutive_failures += 1
            print(f" ✗ no data (fail {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
            results[sym] = {"old": old_data, "new": None, "comparison": None, "tracked_alerts": [], "saved_path": None}
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n  ⛔ {MAX_CONSECUTIVE_FAILURES} consecutive failures — possible IP block. Aborting batch.")
                print("    Recovery: switch network, restart Chrome, retry.")
                break
            continue

        # Success — reset failure counter
        consecutive_failures = 0

        comparison = _compare_shareholding(old_data, new_data)
        tracked_alerts = _detect_tracked_investor_entries(
            new_data.get("individual", {}), tracked_names, new_data.get("quarters", [])
        )

        # Save new file
        company_name = new_data.get("company_name", company.get("name", sym))
        saved_path = _save_shareholding_file(sym, company_name, new_data, tracked_alerts)

        # Status message
        n_q = len(new_data.get("quarters", []))
        if comparison and comparison.get("type") == "new_quarter":
            print(f" ✓ {n_q}q NEW: {comparison['new_quarter']}", end="")
        elif comparison and comparison.get("changes"):
            print(f" ✓ {n_q}q CHANGED", end="")
        else:
            print(f" ✓ {n_q}q no change", end="")
        if tracked_alerts:
            print(f"  🔔 {len(tracked_alerts)} tracked investors")
        else:
            print()

        results[sym] = {
            "old": old_data, "new": new_data, "comparison": comparison,
            "tracked_alerts": tracked_alerts, "saved_path": str(saved_path),
        }

    return results


# ── Helpers ──────────────────────────────────────────────────────────────────

def _inr(amount: float, sign: bool = False) -> str:
    neg = amount < 0
    s = f"₹{abs(amount):,.0f}"
    if sign:
        return f"{'−' if neg else '+'}{s}"
    return f"{'−' if neg else ''}{s}"


def _pct(v: float, sign: bool = True) -> str:
    return f"{'+' if (sign and v >= 0) else ''}{v:.1f}%"


def _is_sme(symbol: str, exchange: str = "") -> bool:
    from workflows.shared_sections import is_sme
    return is_sme(symbol, exchange)


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_all_companies() -> list[dict]:
    companies: list[dict] = []
    seen: set[str] = set()
    for yaml_file in ["watchlist.yaml", "portfolio_companies.yaml"]:
        p = PROJECT_ROOT / yaml_file
        if not p.exists():
            continue
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        for c in data.get("stocks", []):
            sym = c.get("symbol", "").upper()
            if sym and sym not in seen and sym not in ETF_SYMBOLS:
                seen.add(sym)
                companies.append(c)
    return companies


def _load_watchlist_symbols() -> set[str]:
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if not wl_path.exists():
        return set()
    with open(wl_path) as f:
        data = yaml.safe_load(f) or {}
    return {s["symbol"].upper() for s in data.get("stocks", [])}


def _load_holdings(user_id: str | None = None) -> tuple[list[dict], str, Path]:
    """Load latest holdings. Returns (holdings, snapshot_date, user_dir)."""
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        return [], "", PORTFOLIO_DIR

    if user_id:
        user_dir = next((d for d in dirs if d.name.upper() == user_id.upper()), None)
        if not user_dir:
            return [], "", PORTFOLIO_DIR
    elif len(dirs) == 1:
        user_dir = dirs[0]
    else:
        # Multiple users: pick the one with most recent holdings
        best, best_date = dirs[0], ""
        for d in dirs:
            hd = d / "holdings"
            if not hd.exists():
                continue
            dates = sorted((sd.name for sd in hd.iterdir() if sd.is_dir()), reverse=True)
            if dates and dates[0] > best_date:
                best, best_date = d, dates[0]
        user_dir = best

    holdings_dir = user_dir / "holdings"
    if not holdings_dir.exists():
        return [], "", user_dir

    date_dirs = sorted(
        [d for d in holdings_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name, reverse=True,
    )
    if not date_dirs:
        return [], "", user_dir

    latest = date_dirs[0]
    hf = latest / "holdings.json"
    if not hf.exists():
        return [], latest.name, user_dir

    with open(hf) as f:
        holdings = json.load(f)
    return holdings, latest.name, user_dir


# ── State management ────────────────────────────────────────────────────────

def _snapshot_company(symbol: str) -> dict:
    """Snapshot current data state for a company."""
    company_dir = COMPANIES_DIR / symbol.upper()
    snap: dict = {}
    for subdir in TRACKED_DIRS:
        d = company_dir / subdir
        if not d.exists():
            snap[subdir] = {"count": 0, "latest_mtime": 0}
            continue
        files = [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")]
        latest_mtime = max((f.stat().st_mtime for f in files), default=0)
        snap[subdir] = {"count": len(files), "latest_mtime": round(latest_mtime, 1)}
    return snap


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_run": None, "companies": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _compute_deltas(
    prev_state: dict,
    current_snapshots: dict[str, dict],
) -> dict[str, dict]:
    """Compare previous state with current snapshots.
    Returns {symbol: {subdir: change_type}}."""
    prev_companies = prev_state.get("companies", {})
    deltas: dict[str, dict] = {}

    for sym, current in current_snapshots.items():
        prev = prev_companies.get(sym, {})
        changes: dict[str, str] = {}
        for subdir in TRACKED_DIRS:
            curr_data = current.get(subdir, {"count": 0, "latest_mtime": 0})
            prev_data = prev.get(subdir, {"count": 0, "latest_mtime": 0})
            if curr_data["count"] > prev_data["count"]:
                changes[subdir] = "new_files"
            elif curr_data["latest_mtime"] > prev_data["latest_mtime"] + 1:
                changes[subdir] = "updated"
        if changes:
            deltas[sym] = changes

    return deltas


# ── DMA fetch ────────────────────────────────────────────────────────────────

def _fetch_dma_batch(
    symbols: list[str],
    holdings_map: dict[str, dict],
    periods: tuple[int, ...] = (40, 100),
) -> dict[str, dict]:
    """Fetch DMA40 + DMA100 for given symbols.  Cached daily."""
    from skills.dma_fetcher import get_dma  # noqa: PLC0415

    cache_file = CACHE_DIR / f"dma_season_{date.today().isoformat()}.json"
    cache: dict = {}
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())

    results: dict[str, dict] = {}
    missing = [
        s for s in symbols
        if s not in cache or not (cache[s].get("dma100") or cache[s].get("dma40"))
    ]

    if missing:
        print(f"  Fetching DMA40+DMA100 for {len(missing)} stocks...")
    for i, sym in enumerate(missing, 1):
        h = holdings_map.get(sym, {})
        exch = h.get("exchange", "NSE")
        result = get_dma(sym, exch, periods=periods, verbose=False)
        cache[sym] = {
            "dma40": result.get("dma40"),
            "dma100": result.get("dma100"),
            "last_price": result.get("last_price"),
            "source": result.get("source", "none"),
        }
        if i % 20 == 0:
            print(f"    {i}/{len(missing)} done...")

    if missing:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
        resolved = sum(1 for s in missing if cache.get(s, {}).get("dma100") or cache.get(s, {}).get("dma40"))
        print(f"  DMA resolved: {resolved}/{len(missing)}")

    for sym in symbols:
        results[sym] = cache.get(sym, {"dma40": None, "dma100": None, "source": "none"})
    return results


# ── Data fetching (optional) ─────────────────────────────────────────────────

def _fetch_transcripts(companies: list[dict]) -> dict[str, int]:
    """Fetch latest concall transcripts for all companies (no CDP needed)."""
    from llm.client import get_config  # noqa: PLC0415
    from skills import concall_fetcher  # noqa: PLC0415

    config = get_config()
    results: dict[str, int] = {}

    for i, company in enumerate(companies, 1):
        sym = company.get("symbol", "").upper()
        print(f"  [{i}/{len(companies)}] {sym}...", end=" ", flush=True)
        try:
            result = concall_fetcher.run(company, config)
            n_new = len(result.get("data", {}).get("downloaded", []))
            results[sym] = n_new
            print(f"{n_new} new" if n_new else "up to date")
        except Exception as e:
            print(f"error: {e}")
            results[sym] = 0
        time.sleep(0.5)

    return results


def _fetch_all_data(companies: list[dict]) -> None:
    """Full data fetch including CDP-dependent steps."""
    from skills.cdp_helper import is_available as _cdp_ok  # noqa: PLC0415

    cdp = _cdp_ok()
    if not cdp:
        print("  ⚠️  Chrome CDP not detected — financials, shareholding, insights SKIPPED")
        print("  Only transcripts + news will be fetched.\n")

    for i, company in enumerate(companies, 1):
        sym = company.get("symbol", "").upper()
        print(f"\n  [{i}/{len(companies)}] {sym}")
        try:
            from workflows.data_fetch import run as data_fetch_run  # noqa: PLC0415
            data_fetch_run(
                symbol=sym,
                fetch_transcripts=True,
                fetch_news=True,
                fetch_shareholding=cdp,
                fetch_financials=cdp,
                fetch_insights=cdp,
            )
        except Exception as e:
            print(f"    [!] {sym}: {e}")
        time.sleep(1)


# ── Trend classification ─────────────────────────────────────────────────────

def _classify_trend(
    price: float,
    dma40: float | None,
    dma100: float | None,
) -> tuple[str, str]:
    """Classify trend based on price vs DMA40 and DMA100.
    Returns (emoji, label)."""
    if dma40 is None or dma100 is None:
        return "⚪", "No data"
    if price > dma40 and dma40 > dma100:
        return "🟢", "Strong Bullish"
    if price > dma100 and price <= dma40:
        return "🔵", "Bullish Pullback"
    if abs(price - dma100) / dma100 * 100 < 3:
        return "🟡", "Neutral"
    if price < dma100 and price >= dma40:
        return "🟠", "Bearish Rally"
    if price < dma100:
        return "🔴", "Bearish"
    return "🟡", "Neutral"


# ── Financial parser (for quarterly results comparison) ──────────────────────

def _parse_quarterly_financials(symbol: str) -> dict[str, dict[str, float | None]]:
    """Parse quarterly results from the latest financial data file.
    Returns {quarter_label: {metric: value}} e.g. {'Mar 2025': {'Sales': 376, ...}}."""
    fin_dir = COMPANIES_DIR / symbol.upper() / "financials"
    if not fin_dir.exists():
        return {}
    files = sorted(fin_dir.glob(f"{symbol.upper()}_financials_*.txt"), reverse=True)
    if not files:
        return {}
    text = files[0].read_text(encoding="utf-8")

    # Find Quarterly Results section
    match = re.search(r"## Quarterly Results\s*\n", text)
    if not match:
        return {}
    section_start = match.end()
    next_section = re.search(r"\n---\n|\n## ", text[section_start:])
    section_end = section_start + next_section.start() if next_section else len(text)
    section = text[section_start:section_end]

    slines = section.strip().split("\n")
    if not slines:
        return {}

    # First line is header: " | Dec 2022 | Mar 2023 | ..."
    header = slines[0]
    quarters = [q.strip() for q in header.split("|")[1:] if q.strip()]

    METRICS = {
        "Sales +": "Sales",
        "OPM %": "OPM%",
        "Net Profit +": "Net Profit",
        "EPS in Rs": "EPS",
    }
    result: dict[str, dict[str, float | None]] = {q: {} for q in quarters}

    for line in slines[1:]:
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        metric_raw = parts[0].strip()
        metric_name = METRICS.get(metric_raw)
        if not metric_name:
            continue
        values = [v.strip() for v in parts[1:]]
        for i, q in enumerate(quarters):
            if i < len(values):
                try:
                    val = values[i].replace("%", "").replace(",", "").strip()
                    result[q][metric_name] = float(val) if val else None
                except (ValueError, IndexError):
                    result[q][metric_name] = None
    return result


def _quarter_offsets(quarter_str: str) -> tuple[str, str, str]:
    """Given 'Mar 2026', return (prev_quarter, yoy_quarter, prev_half_year).
    prev_quarter: 'Dec 2025', yoy: 'Mar 2025', prev_half: 'Sep 2025'."""
    prev_q_map = {"Mar": ("Dec", -1), "Jun": ("Mar", 0), "Sep": ("Jun", 0), "Dec": ("Sep", 0)}
    half_map = {"Mar": ("Sep", -1), "Sep": ("Mar", 0)}

    parts = quarter_str.split()
    if len(parts) != 2:
        return "", "", ""
    month, year = parts[0], int(parts[1])

    # Previous quarter
    pq_month, pq_delta = prev_q_map.get(month, ("", 0))
    prev_q = f"{pq_month} {year + pq_delta}" if pq_month else ""

    # Year-on-year
    yoy_q = f"{month} {year - 1}"

    # Previous half-year (for SME)
    if month in half_map:
        ph_month, ph_delta = half_map[month]
        prev_half = f"{ph_month} {year + ph_delta}"
    else:
        prev_half = ""

    return prev_q, yoy_q, prev_half


def _build_company_results_table(
    symbol: str,
    name: str,
    financial_data: dict[str, dict[str, float | None]],
    expected_q: str,
    sme: bool,
) -> list[str]:
    """Build a per-company quarterly results comparison table.
    Regular: | | Mar 2025 | Dec 2025 | Mar 2026 | QoQ | YoY |
    SME:     | | Mar 2025 | Sep 2025 | Mar 2026 | HoH | YoY |"""
    prev_q, yoy_q, prev_half = _quarter_offsets(expected_q)
    metrics = ["Sales", "OPM%", "Net Profit", "EPS"]

    if sme and prev_half:
        col_qs = [yoy_q, prev_half, expected_q]
        col_labels = [yoy_q, prev_half, expected_q, "HoH", "YoY"]
        comp_q = prev_half  # HoH comparison
    else:
        col_qs = [yoy_q, prev_q, expected_q]
        col_labels = [yoy_q, prev_q, expected_q, "QoQ", "YoY"]
        comp_q = prev_q  # QoQ comparison

    # Show a section if we have any quarterly data at all (even if prev/yoy are missing)
    has_data = bool(financial_data)
    if not has_data:
        return []

    lines = [
        f"### {name} ({symbol})",
        "| | " + " | ".join(col_labels) + " |",
        "|---|" + "---|" * len(col_labels),
    ]

    for metric in metrics:
        vals = []
        for q in col_qs:
            v = financial_data.get(q, {}).get(metric)
            if v is not None:
                vals.append(v)
                if metric == "OPM%":
                    vals[-1] = v  # keep as-is for display
            else:
                vals.append(None)

        # Format values
        def _fmt(v: float | None, is_pct: bool = False) -> str:
            if v is None:
                return "—"
            return f"{v:.0f}%" if is_pct else (f"{v:.2f}" if abs(v) < 10 else f"{v:,.0f}")

        is_pct = metric == "OPM%"
        current_v, comp_v, yoy_v = vals[2], vals[1], vals[0]

        # Compute changes
        def _change(curr: float | None, prev: float | None, is_pct_metric: bool = False) -> str:
            if curr is None or prev is None:
                return "—"
            if is_pct_metric:
                delta = curr - prev
                arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "→")
                return f"{arrow}{abs(delta):.1f}pp"
            if prev == 0:
                return "—"
            chg = (curr - prev) / abs(prev) * 100
            arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "→")
            return f"{arrow}{abs(chg):.0f}%"

        qoq_str = _change(current_v, comp_v, is_pct)
        yoy_str = _change(current_v, yoy_v, is_pct)

        row = [metric]
        for v in vals:
            row.append(_fmt(v, is_pct))
        row += [qoq_str, yoy_str]
        lines.append("| " + " | ".join(row) + " |")
 
    lines.append("")
    return lines


# ── Investor matrix ──────────────────────────────────────────────────────────

def _abbreviate_investor(name: str) -> str:
    """Shorten investor names for matrix column headers."""
    replacements = {
        "Pvt Ltd": "", "Private Limited": "", "Limited": "",
        "Securities": "Sec", "Finance And Investment": "Fin",
        "Commotrade": "Comm",
    }
    short = name
    for old, new in replacements.items():
        short = short.replace(old, new)
    parts = short.strip().split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}"
    if len(parts) == 2:
        return f"{parts[0]} {parts[1][0]}"
    return short[:15]


def _build_investor_matrix(
    sh_results: dict[str, dict],
    company_map: dict[str, dict],
    expected_q: str,
) -> list[str]:
    """Build tracked investor matrix: companies as rows, investors as columns.
    Cell = latest% with trend arrow."""
    # Collect all (company, alert) tuples
    entries: list[tuple[str, dict]] = []
    for sym, r in sh_results.items():
        for alert in r.get("tracked_alerts", []):
            entries.append((sym, alert))

    if not entries:
        return []

    # Get unique investors and their abbreviations
    investor_counts: dict[str, int] = {}
    for _, alert in entries:
        inv = alert["matched_name"]
        investor_counts[inv] = investor_counts.get(inv, 0) + 1

    sorted_investors = sorted(investor_counts.keys(), key=lambda x: -investor_counts[x])
    inv_abbrevs = {inv: _abbreviate_investor(inv) for inv in sorted_investors}

    # Build matrix data: {sym: {investor: alert}}
    matrix: dict[str, dict[str, dict]] = {}
    for sym, alert in entries:
        matrix.setdefault(sym, {})[alert["matched_name"]] = alert

    # Build markdown table
    headers = ["Company"] + [inv_abbrevs[i] for i in sorted_investors]
    lines = [
        "### 🔔 Tracked Investor Matrix",
        f"*{len(matrix)} companies × {len(sorted_investors)} tracked investors*\n",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]

    # Filter to rows that have at least one value for the expected quarter
    filtered_syms = []
    for sym in sorted(matrix.keys()):
        c = company_map.get(sym, {})
        name = c.get("name", sym)[:20]
        row = [f"{name} ({sym})"]
        any_val = False
        for inv in sorted_investors:
            alert = matrix[sym].get(inv)
            if not alert:
                row.append("—")
            else:
                # Show value if alert quarter is expected or newer
                def _q_tuple(q: str) -> tuple:
                    _M = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                    p = q.strip().split()
                    return (int(p[1]), _M.get(p[0].lower()[:3], 0)) if len(p) == 2 else (0, 0)
                if _q_tuple(alert.get("latest_q", "")) < _q_tuple(expected_q):
                    row.append("—")
                else:
                    arrow = {"NEW": "🆕", "EXIT": "🚪", "UP": "↑", "DOWN": "↓", "STABLE": "→"}.get(alert["trend"], "?")
                    if alert["trend"] == "EXIT":
                        row.append(f"0%🚪(was {alert['prev_pct']:.1f}%)")
                    else:
                        row.append(f"{alert['latest_pct']:.2f}%{arrow}")
                    any_val = True
        if any_val:
            filtered_syms.append(sym)
            lines.append("| " + " | ".join(row) + " |")

    if len(lines) >= 2:
        lines[1] = f"*{len(filtered_syms)} companies × {len(sorted_investors)} tracked investors*\n"
    lines.append("")
    return lines


# ── Investor group mapping (parent → fund houses) ────────────────────────────

INVESTOR_GROUPS: dict[str, list[str]] = {
    "Ashish Kacholia": [
        "Ashish Kacholia",
        "Bengal Finance And Investment Pvt Ltd",
        "Suryavanshi Commotrade Private Limited",
        "Dar'S Business Finance Pvt Ltd",
    ],
    "Mukul Agarwal": [
        "Mukul Mahavir Agarwal",
        "Mukul Mahavir Agrawal",
        "Sanshi Fund-I",
        "Param Capital",
        "Ms Param Capital",
        "Param Value Investments",
    ],
    "Vijay Kedia": [
        "Vijay Kedia",
        "Kedia Securities Private Limited",
    ],
}

# Build reverse map: entity name → parent investor name
_ENTITY_TO_PARENT: dict[str, str] = {}
for _parent, _entities in INVESTOR_GROUPS.items():
    for _e in _entities:
        _ENTITY_TO_PARENT[_normalize_investor_name(_e)] = _parent
# For standalone investors (not in a group), they map to themselves


def _resolve_parent_investor(entity_name: str) -> str:
    """Map a fund house / entity name to the parent investor."""
    norm = _normalize_investor_name(entity_name)
    return _ENTITY_TO_PARENT.get(norm, entity_name)


def _get_market_cap(symbol: str) -> float | None:
    """Extract market cap (in Cr) from the latest financials file for a company."""
    fin_dir = COMPANIES_DIR / symbol.upper() / "financials"
    if not fin_dir.exists():
        return None
    files = sorted(fin_dir.glob(f"{symbol.upper()}_financials_*.txt"), reverse=True)
    if not files:
        return None
    text = files[0].read_text(encoding="utf-8", errors="ignore")
    # Match: "Market Cap ₹ 3,909 Cr." or "Market Cap  ₹ 779 Cr.  779"
    m = re.search(r"Market\s+Cap\s+₹?\s*([\d,]+(?:\.\d+)?)\s*Cr", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _build_allocation_table(
    sh_results: dict[str, dict],
    company_map: dict[str, dict],
    expected_q: str,
) -> list[str]:
    """Build investment allocation table: companies × parent investors.
    Value = holding% × market_cap (in ₹ Cr), grouped by parent investor.
    Returns markdown lines to append to shareholding.md."""

    # Collect all individual holder data with investment amounts
    # Structure: { symbol: { parent_investor: investment_cr } }
    company_investments: dict[str, dict[str, float]] = {}
    all_parents: dict[str, float] = {}  # parent → total investment across all cos

    for sym, r in sh_results.items():
        mcap = _get_market_cap(sym)
        if not mcap:
            continue
        alerts = r.get("tracked_alerts", [])
        if not alerts:
            continue
        sym_inv: dict[str, float] = {}
        for alert in alerts:
            pct = alert.get("latest_pct", 0)
            if not pct or pct <= 0:
                continue
            entity = alert.get("investor", alert.get("matched_name", ""))
            parent = _resolve_parent_investor(entity)
            invest_cr = pct / 100.0 * mcap
            sym_inv[parent] = sym_inv.get(parent, 0) + invest_cr
        if sym_inv:
            company_investments[sym] = sym_inv
            for parent, amt in sym_inv.items():
                all_parents[parent] = all_parents.get(parent, 0) + amt

    if not company_investments:
        return []

    # Sort parents by total investment descending
    sorted_parents = sorted(all_parents.keys(), key=lambda p: -all_parents[p])

    # Sort companies by max investment from any single parent
    def _max_inv(sym: str) -> float:
        return max(company_investments[sym].values()) if company_investments.get(sym) else 0

    sorted_syms = sorted(company_investments.keys(), key=lambda s: -_max_inv(s))

    # Build table
    headers = ["Company", "Mcap (Cr)"] + [p.split()[0][:12] for p in sorted_parents] + ["Max Inv (Cr)"]
    lines = [
        "\n### 💰 Anchor Investor Allocation",
        f"*Investment = holding% × market cap — {len(sorted_syms)} companies × {len(sorted_parents)} investors*\n",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for sym in sorted_syms:
        c = company_map.get(sym, {})
        name = c.get("name", sym)[:25]
        mcap = _get_market_cap(sym)
        mcap_str = f"{mcap:,.0f}" if mcap else "—"
        inv_map = company_investments[sym]
        cells = []
        for parent in sorted_parents:
            amt = inv_map.get(parent, 0)
            cells.append(f"{amt:.1f}" if amt > 0 else "—")
        max_inv = max(inv_map.values())
        cells.append(f"**{max_inv:.1f}**")
        lines.append(f"| {name} ({sym}) | {mcap_str} | " + " | ".join(cells) + " |")

    # Totals row
    total_cells = []
    for parent in sorted_parents:
        total_cells.append(f"**{all_parents[parent]:.1f}**")
    total_cells.append(f"**{sum(all_parents.values()):.1f}**")
    lines.append(f"| **TOTAL** | | " + " | ".join(total_cells) + " |")
    lines.append("")

    return lines


def _write_anchor_alignment(
    sh_results: dict[str, dict],
    company_map: dict[str, dict],
    holdings_map: dict[str, dict],
    user_id: str,
    expected_q: str,
) -> Path | None:
    """Write/update the anchor_alignment.md file in portfolio/{user_id}/.
    Scans ALL holdings (+ tracked companies) for anchor investors using
    the latest shareholding file on disk — not limited to sh_results.
    Table 1: Companies ranked by max anchor investment (grouped by parent).
    Table 2: Portfolio adjustment suggestions (under → over allocated)."""
    user_dir = PORTFOLIO_DIR / user_id
    if not user_dir.exists():
        return None

    today = date.today().isoformat()
    tracked_names = _load_tracked_investors()
    if not tracked_names:
        return None

    # ── Scan ALL known companies for tracked investors ────────────────────
    # Merge symbols from: sh_results, portfolio holdings, and company_map
    all_syms = set(sh_results.keys()) | set(holdings_map.keys()) | set(company_map.keys())

    # { symbol: { parent: investment_cr } }
    company_investments: dict[str, dict[str, float]] = {}
    parent_totals: dict[str, float] = {}  # parent → total across all companies

    for sym in sorted(all_syms):
        mcap = _get_market_cap(sym)
        if not mcap:
            continue

        # Try to get tracked alerts from sh_results first (freshest data)
        alerts: list[dict] = []
        sr = sh_results.get(sym)
        if sr and sr.get("tracked_alerts"):
            alerts = sr["tracked_alerts"]
        else:
            # Fall back to latest shareholding file on disk
            parsed = _parse_existing_shareholding(sym)
            if parsed and parsed.get("individual"):
                alerts = _detect_tracked_investor_entries(
                    parsed["individual"], tracked_names, parsed.get("quarters", [])
                )

        if not alerts:
            continue

        sym_inv: dict[str, float] = {}
        for alert in alerts:
            pct = alert.get("latest_pct", 0)
            if not pct or pct <= 0:
                continue
            entity = alert.get("investor", alert.get("matched_name", ""))
            parent = _resolve_parent_investor(entity)
            invest_cr = pct / 100.0 * mcap
            sym_inv[parent] = sym_inv.get(parent, 0) + invest_cr
        if sym_inv:
            company_investments[sym] = sym_inv
            for parent, amt in sym_inv.items():
                parent_totals[parent] = parent_totals.get(parent, 0) + amt

    if not company_investments:
        return None

    # ── Compute max investment per company from any single parent ───────
    anchor_details: dict[str, tuple[str, float]] = {}  # sym → (best_parent, best_amt)
    for sym, inv_map in company_investments.items():
        best_parent = max(inv_map, key=inv_map.get)
        anchor_details[sym] = (best_parent, inv_map[best_parent])

    # ── Investment rank (1 = highest) ────────────────────────────────────
    inv_sorted = sorted(anchor_details.keys(), key=lambda s: -anchor_details[s][1])
    inv_rank: dict[str, int] = {s: i + 1 for i, s in enumerate(inv_sorted)}

    # ── My portfolio allocation ─────────────────────────────────────────
    total_portfolio = sum(h.get("current_value", 0) for h in holdings_map.values())
    my_alloc: dict[str, float] = {}  # sym → % of my portfolio
    for sym, h in holdings_map.items():
        val = h.get("current_value", 0)
        my_alloc[sym] = (val / total_portfolio * 100) if total_portfolio > 0 else 0

    # My allocation rank among anchor-held stocks (1 = highest alloc)
    anchor_syms_by_my_alloc = sorted(inv_sorted, key=lambda s: -my_alloc.get(s, 0))
    my_rank: dict[str, int] = {s: i + 1 for i, s in enumerate(anchor_syms_by_my_alloc)}

    sorted_parents = sorted(parent_totals.keys(), key=lambda p: -parent_totals[p])

    # ── Header ───────────────────────────────────────────────────────────
    lines = [
        f"# Anchor Investor Alignment — {expected_q}",
        f"*Updated: {today}*\n",
        "## Methodology",
        "- **Investment** = individual holder % × market cap (Cr)",
        "- **Parent grouping**: fund houses consolidated under managing investor",
        f"  - Ashish Kacholia = Ashish + Bengal Finance + Suryavanshi + Dar's",
        f"  - Mukul Agarwal = Mukul + Sanshi Fund + Param Capital",
        f"  - Vijay Kedia = Vijay + Kedia Securities",
        "- **Inv Rank**: company ranked by max anchor investment (1 = highest conviction)",
        "- **My Rank**: same companies ranked by my allocation (1 = my highest alloc)",
        "- **Rank Gap**: My Rank − Inv Rank (positive = I'm underweight vs anchor)\n",
    ]

    # ── Table 1: Anchor-held companies by investment ─────────────────────
    lines.append("## Table 1: Anchor-Held Companies by Investment")
    lines.append(f"*{len(inv_sorted)} companies held by tracked investors, sorted by max investment*\n")
    t1_headers = ["#", "Company", "Mcap(Cr)", "Top Investor", "Invest(Cr)", "My%", "My Rank"]
    lines.append("| " + " | ".join(t1_headers) + " |")
    lines.append("|" + "---|" * len(t1_headers))

    for sym in inv_sorted:
        c = company_map.get(sym, {})
        name = c.get("name", sym)[:28]
        mcap = _get_market_cap(sym)
        mcap_str = f"{mcap:,.0f}" if mcap else "—"
        parent, invest = anchor_details[sym]
        my_pct = my_alloc.get(sym, 0)
        mr = my_rank[sym]
        ir = inv_rank[sym]
        sme = "ˢᵐᵉ" if any(sym.upper().endswith(s) for s in SME_SUFFIXES) else ""
        lines.append(
            f"| {ir} | {name} ({sym}){sme} | {mcap_str} | {parent.split()[0][:10]} "
            f"| {invest:.1f} | {my_pct:.2f}% | {mr} |"
        )
    lines.append("")

    # ── Table 2: Adjustment suggestions (rank-gap based) ─────────────────
    # For anchor-held: rank_gap = my_rank − inv_rank (positive → underweight)
    # For non-anchor holdings ≥ 1%: shown separately at bottom
    anchor_rows: list[tuple[str, int, float, int, float, int, float]] = []
    # (sym, inv_rank, invest_cr, my_rank, my_pct, rank_gap, current_value)

    for sym in inv_sorted:
        ir = inv_rank[sym]
        mr = my_rank[sym]
        rg = mr - ir
        _, invest = anchor_details[sym]
        my_pct = my_alloc.get(sym, 0)
        val = holdings_map.get(sym, {}).get("current_value", 0)
        anchor_rows.append((sym, ir, invest, mr, my_pct, rg, val))

    # Sort: underweight first (rank_gap desc), then overweight
    anchor_rows.sort(key=lambda r: -r[5])

    # Non-anchor holdings ≥ 1%
    non_anchor_rows: list[tuple[str, float, float]] = []  # sym, my_pct, val
    for sym, h in holdings_map.items():
        if sym in company_investments:
            continue
        my_pct = my_alloc.get(sym, 0)
        if my_pct >= 1.0:
            non_anchor_rows.append((sym, my_pct, h.get("current_value", 0)))
    non_anchor_rows.sort(key=lambda r: -r[1])

    # Count categories
    underweight = sum(1 for r in anchor_rows if r[5] > 3)
    overweight = sum(1 for r in anchor_rows if r[5] < -3)
    aligned = sum(1 for r in anchor_rows if -3 <= r[5] <= 3)

    lines.append("## Table 2: Portfolio Adjustment Suggestions")
    lines.append(f"*Rank gap = My Rank − Inv Rank — positive means I'm underweight*\n")
    lines.append(f"- **Underweight** (gap > 3): {underweight} companies")
    lines.append(f"- **Overweight** (gap < −3): {overweight} companies")
    lines.append(f"- **Aligned** (|gap| ≤ 3): {aligned} companies")
    lines.append(f"- **No anchor backing** (≥1% alloc): {len(non_anchor_rows)} companies\n")

    t2_headers = ["Company", "Invest(Cr)", "Inv#", "My%", "My#", "Gap", "Action"]
    lines.append("| " + " | ".join(t2_headers) + " |")
    lines.append("|" + "---|" * len(t2_headers))

    for sym, ir, invest, mr, my_pct, rg, val in anchor_rows:
        c = company_map.get(sym, {})
        name = c.get("name", sym)[:28]
        sme = "ˢᵐᵉ" if any(sym.upper().endswith(s) for s in SME_SUFFIXES) else ""
        gap_str = f"+{rg}" if rg > 0 else str(rg)

        if my_pct == 0:
            action = "⬆️ **NEW POSITION** — not held"
        elif rg > 10:
            action = "⬆️ **INCREASE** significantly"
        elif rg > 5:
            action = "⬆️ Increase"
        elif rg > 3:
            action = "↗️ Slight increase"
        elif rg < -10:
            action = "⬇️ **REDUCE** significantly"
        elif rg < -5:
            action = "⬇️ Reduce"
        elif rg < -3:
            action = "↘️ Slight reduce"
        else:
            action = "✅ Aligned"

        lines.append(
            f"| {name} ({sym}){sme} | {invest:.1f} | {ir} "
            f"| {my_pct:.2f}% | {mr} | {gap_str} | {action} |"
        )

    # Non-anchor holdings section
    if non_anchor_rows:
        lines.append("")
        lines.append("### Non-Anchor Holdings ≥1%")
        lines.append(f"*{len(non_anchor_rows)} positions with no tracked anchor backing*\n")
        lines.append("| Company | My% | Current(₹) | Note |")
        lines.append("|---|---|---|---|")
        for sym, my_pct, val in non_anchor_rows:
            c = company_map.get(sym, {})
            name = c.get("name", sym)[:28]
            sme = "ˢᵐᵉ" if any(sym.upper().endswith(s) for s in SME_SUFFIXES) else ""
            val_str = f"₹{val:,.0f}" if val > 0 else "—"
            lines.append(f"| {name} ({sym}){sme} | {my_pct:.2f}% | {val_str} | No anchor backing |")
    lines.append("")

    # ── Summary stats ────────────────────────────────────────────────────
    total_my_in_anchor = sum(my_alloc.get(s, 0) for s in company_investments)
    non_anchor_alloc = sum(my_alloc.get(s, 0) for s in holdings_map if s not in company_investments)
    lines.append("## Summary")
    lines.append(f"- **Anchor-tracked companies**: {len(company_investments)}")
    lines.append(f"- **My portfolio total**: ₹{total_portfolio:,.0f}")
    lines.append(f"- **My allocation in anchor stocks**: {total_my_in_anchor:.1f}% of portfolio")
    lines.append(f"- **My allocation in non-anchor stocks**: {non_anchor_alloc:.1f}% ({len(non_anchor_rows)} stocks ≥1%)")
    for parent in sorted_parents:
        cos = sum(1 for s in company_investments if parent in company_investments[s])
        lines.append(f"- **{parent}** tracked: ₹{parent_totals[parent]:.1f} Cr across {cos} companies")
    lines.append("")

    out = user_dir / "anchor_alignment.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ── Quarterly evolving file writers ──────────────────────────────────────────

def _write_shareholding_quarterly(
    expected_q: str,
    sh_results: dict[str, dict],
    company_map: dict[str, dict],
    holdings_map: dict[str, dict],
) -> Path:
    """Write/update the evolving shareholding.md for the current quarter.
    Contains: shareholding changes table + tracked investor matrix."""
    quarter_dir = EARNINGS_SEASON_DIR / expected_q.replace(" ", "")
    quarter_dir.mkdir(parents=True, exist_ok=True)
    out = quarter_dir / "shareholding.md"

    today = date.today().isoformat()

    # Helper: parse existing table rows keyed by symbol from prior file
    def _parse_existing_rows(text: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        if not text:
            return rows
        # Locate Shareholding Changes section
        m = re.search(r"^## Shareholding Changes\s*$", text, re.MULTILINE)
        if not m:
            return rows
        start = m.end()
        # Find next H2 section or EOF — stop ONLY at H2 (##), not H3 subsections
        m2 = re.search(r"^## ", text[start:], re.MULTILINE)
        end = start + m2.start() if m2 else len(text)
        block = text[start:end]
        # Collect markdown table rows (skip headers/separators)
        for line in block.splitlines():
            line = line.rstrip()
            if not line.startswith("|"):
                continue
            if line.strip().startswith("|---"):
                continue
            if line.strip().startswith("| Company "):
                continue
            # Accept only rows that have exactly 7 data cells (our table: Company, Quarter, Promoter, FII, DII, Public, Alert)
            cells = [c.strip() for c in line.strip().split("|")[1:-1]]
            if len(cells) != 7:
                continue
            # extract symbol in parentheses for stable key
            m_sym = re.search(r"\(([^)]+)\)", line)
            key = m_sym.group(1).strip().upper() if m_sym else line
            rows[key] = line
        return rows

    out_text_old = out.read_text(encoding="utf-8") if out.exists() else ""

    # Determine if we have any new content to add this run
    sh_changed = {
        sym: r for sym, r in sh_results.items()
        if r.get("comparison") and (r["comparison"].get("changes") or r["comparison"].get("type") == "new_quarter")
    }
    any_tracked = any(len(r.get("tracked_alerts", [])) for r in sh_results.values())

    # Note: even if there are no new changes/alerts, we will still rebuild the file below
    # so that the Pending section is always up to date.

    # Build new rows for changed companies in 7-column format (with Alert)
    def _build_row(sym: str, r: dict) -> str:
        comp = r["comparison"]
        c = company_map.get(sym, {})
        name = c.get("name", sym)
        q_label = f"NEW: {comp.get('new_quarter', '')}" if comp.get("type") == "new_quarter" else comp.get("latest_quarter", "")
        changes = comp.get("changes", {})
        latest = comp.get("latest", {})

        def _fmt_sh(cat: str) -> str:
            if cat in changes:
                ch = changes[cat]
                delta = ch.get("delta")
                if delta is not None:
                    arrow = "▲" if delta > 0 else "▼"
                    return f"{ch['new']:.2f}% {arrow}{abs(delta):.2f}pp"
                return f"{ch['new']:.2f}%"
            val = latest.get(cat)
            # For companies where expected_q isn't the latest, leave blank for this quarter-level report
            return f"{val:.2f}%" if val is not None and comp.get("latest_quarter") == expected_q else "—"

        alloc_str = f" ({holdings_map[sym]['pct_of_portfolio']:.1f}%)" if sym in holdings_map and "pct_of_portfolio" in holdings_map[sym] else ""
        # Alert column: ALL tracked investors with short format (first word + trend), deduplicated
        ta = r.get("tracked_alerts", [])
        alert = ""
        if ta:
            seen = set()
            parts = []
            for a in ta:
                inv = a.get("matched_name", a.get("investor", ""))
                short = inv.split()[0] if inv else "?"
                trend = str(a.get("trend", "")).upper()
                key = (short, trend)
                if key not in seen:
                    seen.add(key)
                    parts.append(f"{short} {trend}")
            alert = "🔔 " + ", ".join(parts)
        return (
            f"| {name} ({sym}){alloc_str} | {q_label} "
            f"| {_fmt_sh('Promoters+')} | {_fmt_sh('FIIs+')} "
            f"| {_fmt_sh('DIIs+')} | {_fmt_sh('Public+')} | {alert} |"
        )

    new_rows_map: dict[str, str] = {sym: _build_row(sym, r) for sym, r in sh_changed.items()}

    # Merge with existing rows (append-only semantics per symbol)
    merged_rows = _parse_existing_rows(out_text_old)
    merged_rows.update(new_rows_map)  # prefer latest row for symbols we processed now

    # Compose final content
    lines: list[str] = [
        f"# Shareholding — {expected_q}",
        f"*Updated: {today}*\n",
        "## Shareholding Changes",
    ]
    if merged_rows:
        lines.append(f"*{len(merged_rows)} companies with actual changes*\n")
        lines.append("| Company | Quarter | Promoter | FII | DII | Public | Alert |")
        lines.append("|---|---|---|---|---|---|---|")
        for sym in sorted(merged_rows.keys()):
            lines.append(merged_rows[sym])
        lines.append("")
    else:
        lines.append("*No shareholding changes detected yet.*\n")

    # Tracked investor matrix — only if we have fresh alerts; otherwise keep previous content by not rewriting it above
    matrix_lines = _build_investor_matrix(sh_results, company_map, expected_q)
    if matrix_lines:
        lines.extend(matrix_lines)

    # Anchor investor allocation table (holding% × market cap)
    alloc_lines = _build_allocation_table(sh_results, company_map, expected_q)
    if alloc_lines:
        lines.extend(alloc_lines)

    # Pending table — companies missing expected quarter shareholding
    pending: list[tuple[str, str, float]] = []  # (symbol, last_q, alloc%)
    for sym in sorted(company_map.keys()):
        parsed = _parse_existing_shareholding(sym)
        last_q = (parsed.get("quarters", []) or [""])[-1] if parsed else ""
        if last_q != expected_q:
            alloc = holdings_map.get(sym, {}).get("pct_of_portfolio", 0.0)
            pending.append((sym, last_q or "—", alloc))
    if pending:
        lines.append("### ⏳ Pending — Missing latest quarter")
        lines.append(f"*{len(pending)} companies still missing {expected_q} shareholding*\n")
        lines.append("| Company | Last Available | Alloc% |")
        lines.append("|---|---|---|")
        for sym, last_q, alloc in sorted(pending, key=lambda x: (-1 if x[0] in holdings_map else 0, x[0])):
            c = company_map.get(sym, {})
            name = c.get("name", sym)
            alloc_str = f"{alloc:.2f}%" if alloc else "—"
            lines.append(f"| {name} ({sym}) | {last_q} | {alloc_str} |")
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _write_results_quarterly(
    expected_q: str,
    all_companies: list[dict],
    company_map: dict[str, dict],
    holdings_map: dict[str, dict],
    deltas: dict[str, dict],
) -> Path:
    """Write/update the evolving results.md for the current quarter.
    Contains: per-company quarterly financial comparison tables."""
    quarter_dir = EARNINGS_SEASON_DIR / expected_q.replace(" ", "")
    quarter_dir.mkdir(parents=True, exist_ok=True)
    out = quarter_dir / "results.md"

    today = date.today().isoformat()
    lines = [
        f"# Quarterly Results — {expected_q}",
        f"*Updated: {today}*\n",
    ]

    # Financial deltas — companies with new financial data
    financial_deltas = {s: d for s, d in deltas.items() if "financials" in d}
    if financial_deltas:
        lines.append(f"*{len(financial_deltas)} companies with new/updated financial data*\n")

    # Build per-company comparison tables for ALL companies (with data)
    companies_with_tables = 0
    held_syms = set(holdings_map.keys())

    # Process held companies first, then watchlist/others
    all_syms = sorted(
        [c.get("symbol", "").upper() for c in all_companies],
        key=lambda s: (-1 if s in held_syms else 0, s),
    )

    for sym in all_syms:
        c = company_map.get(sym, {})
        name = c.get("name", sym)
        sme = _is_sme(sym, c.get("exchange", ""))
        fin_data = _parse_quarterly_financials(sym)
        if not fin_data:
            continue
        table = _build_company_results_table(sym, name, fin_data, expected_q, sme)
        if table:
            lines.extend(table)
            companies_with_tables += 1

    if companies_with_tables == 0:
        lines.append("*No quarterly results data available yet for this quarter.*\n")
    else:
        lines.insert(2, f"*{companies_with_tables} companies with financial comparison*\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _write_concalls_quarterly(
    expected_q: str,
    deltas: dict[str, dict],
    company_map: dict[str, dict],
    holdings_map: dict[str, dict],
    current_snapshots: dict[str, dict],
) -> Path:
    """Write/update the evolving concalls.md for the current quarter.
    Contains: new transcripts and PPTs per company."""
    quarter_dir = EARNINGS_SEASON_DIR / expected_q.replace(" ", "")
    quarter_dir.mkdir(parents=True, exist_ok=True)
    out = quarter_dir / "concalls.md"

    today = date.today().isoformat()
    lines = [
        f"# Concall Updates — {expected_q}",
        f"*Updated: {today}*\n",
    ]

    transcript_deltas = {s: d for s, d in deltas.items() if "transcripts" in d or "ppt" in d}

    if transcript_deltas:
        lines.append(f"## 📝 New Transcripts & PPTs")
        lines.append(f"*{len(transcript_deltas)} companies with new concall materials*\n")
        for sym in sorted(transcript_deltas):
            c = company_map.get(sym, {})
            snap = current_snapshots.get(sym, {})
            tc = snap.get("transcripts", {}).get("count", 0)
            pc = snap.get("ppt", {}).get("count", 0)
            alloc_str = f" · alloc {holdings_map[sym]['pct_of_portfolio']:.1f}%" if sym in holdings_map and "pct_of_portfolio" in holdings_map[sym] else ""
            lines.append(f"- **{c.get('name', sym)} ({sym})** — {tc} transcripts, {pc} PPTs{alloc_str}")
        lines.append("\n*Run transcript analysis: `python -m workflows.full_company_analysis SYMBOL`*\n")
    else:
        lines.append("*No new concall transcripts detected yet.*\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ── Main run ─────────────────────────────────────────────────────────────────

def run(
    user_id: str | None = None,
    fetch: bool = False,
    fetch_all: bool = False,
    fetch_sh: bool = False,
    fetch_fin: bool = False,
    fetch_transcripts: bool = False,
    full_refresh: bool = False,
    symbols: list[str] | None = None,
    onboard: list[str] | None = None,
) -> Path | None:
    today = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  EARNINGS SEASON REPORT  —  {today}")
    print(f"{'='*60}\n")

    # Load data
    all_companies = _load_all_companies()
    watchlist_symbols = _load_watchlist_symbols()
    holdings, snapshot_date, user_dir = _load_holdings(user_id)
    tracked_names = _load_tracked_investors()

    # Filter to only currently-held + watchlist companies (avoids stale YAML entries)
    held_symbols = {h["symbol"] for h in holdings}
    active_symbols = held_symbols | watchlist_symbols

    if symbols:
        syms_upper = {s.upper() for s in symbols}
        target_companies = [c for c in all_companies if c.get("symbol", "").upper() in syms_upper]
    else:
        target_companies = [c for c in all_companies if c.get("symbol", "").upper() in active_symbols]
        skipped = len(all_companies) - len(target_companies)
        if skipped:
            print(f"  ℹ Skipped {skipped} companies not in current holdings or watchlist")

    print(f"  Companies: {len(target_companies)}  |  Holdings: {len(holdings)}")
    print(f"  Snapshot: {snapshot_date or 'none'}")
    print(f"  Tracked investors: {len(tracked_names)}")

    # Load previous state
    prev_state = _load_state() if not full_refresh else {"last_run": None, "companies": {}}
    prev_run_date = prev_state.get("last_run")
    is_first_run = prev_run_date is None
    print(f"  Previous run: {prev_run_date or 'first run (establishing baseline)'}")

    # Phase 0: Onboard new companies if requested
    if onboard:
        print(f"\n── Onboarding {len(onboard)} new companies ──")
        for spec in onboard:
            # spec format: "SYMBOL:Company Name:screener_url" or just "SYMBOL"
            parts = spec.split(":", 2)
            ob_symbol = parts[0].strip().upper()
            ob_name = parts[1].strip() if len(parts) > 1 else ob_symbol
            ob_url = parts[2].strip() if len(parts) > 2 else ""
            print(f"\n  Onboarding {ob_symbol} — {ob_name}...")
            try:
                from workflows.onboard_company import run as onboard_run  # noqa: PLC0415
                onboard_run(
                    symbol=ob_symbol, name=ob_name,
                    screener_url=ob_url, skip_cdp=True, skip_analysis=False,
                )
            except Exception as e:
                print(f"  [!] Onboarding {ob_symbol} failed: {e}")

    # Determine which fetch phases to run
    # --fetch-all overrides everything; --fetch implies all 3; individual flags are additive
    do_sh = fetch_sh or fetch or fetch_all
    do_fin = fetch_fin or fetch or fetch_all
    do_transcripts = fetch_transcripts or fetch or fetch_all

    expected_q_for_flag = _expected_latest_quarter() or f"Mar {date.today().year}"

    # Phase 1a: Fetch shareholding via Chrome CDP (parallel — 3 tabs)
    sh_results: dict[str, dict] = {}
    if do_sh:
        companies_for_sh = []
        for c in target_companies:
            sym = c.get("symbol", "").upper()
            data = _parse_existing_shareholding(sym)
            has_q = data and data.get("quarters") and data["quarters"][-1] == expected_q_for_flag
            if not has_q or fetch_all:
                companies_for_sh.append(c)

        print(f"\n── Fetching shareholding for {len(companies_for_sh)}/{len(target_companies)} companies (parallel CDP) ──")
        if companies_for_sh:
            sh_results = _fetch_shareholding_batch(companies_for_sh, tracked_names, force=fetch_all)
            n_changed = sum(1 for r in sh_results.values() if r.get("comparison") and r["comparison"].get("changes"))
            n_new_q = sum(1 for r in sh_results.values() if r.get("comparison") and r["comparison"].get("type") == "new_quarter")
            n_tracked = sum(len(r.get("tracked_alerts", [])) for r in sh_results.values())
            print(f"  Summary: {n_new_q} new quarters, {n_changed} with changes, {n_tracked} tracked investor alerts")
        else:
            print("  All companies already have latest quarter shareholding — skipped.")

    # Phase 1b: Fetch financials (Chrome CDP, per-company)
    if do_fin:
        print("\n── Ensuring quarterly financials for expected quarter ──")
        try:
            from skills.cdp_helper import is_available as _cdp_avail  # noqa: PLC0415
        except Exception:
            _cdp_avail = lambda: False  # type: ignore
        missing_fin = []
        for c in target_companies:
            sym = c.get("symbol", "").upper()
            fin = _parse_quarterly_financials(sym)
            has_latest = expected_q_for_flag in fin and any(v is not None for v in fin.get(expected_q_for_flag, {}).values())
            if not has_latest or fetch_all:
                missing_fin.append(sym)
        if missing_fin and (_cdp_avail()):
            print(f"  Fetching financials for {len(missing_fin)} companies…")
            try:
                from workflows.data_fetch import run as data_fetch_run  # noqa: PLC0415
                for i, sym in enumerate(missing_fin, 1):
                    print(f"    [{i}/{len(missing_fin)}] {sym} — financials")
                    data_fetch_run(
                        symbol=sym,
                        fetch_transcripts=False,
                        fetch_news=False,
                        fetch_shareholding=False,
                        fetch_financials=True,
                        fetch_insights=False,
                        fetch_valuepickr=False,
                        refresh_financials=False,
                        interactive=False,
                    )
            except Exception as e:
                print(f"  ⚠️ Financials fetch error: {e}")
        else:
            if not missing_fin:
                print("  All companies already have expected quarter financials.")
            else:
                print("  ⚠️ Chrome CDP not available — skipping financials fetch.")

    # Phase 1c: Fetch transcripts (no CDP needed — BSE/Screener AJAX)
    if do_transcripts:
        qdir = EARNINGS_SEASON_DIR / expected_q_for_flag.replace(" ", "")
        flag_path = qdir / "transcripts_flag.md"
        if fetch_all:
            print(f"\n── Fetching ALL data for {len(target_companies)} companies (CDP needed) ──")
            _fetch_all_data(target_companies)
            qdir.mkdir(parents=True, exist_ok=True)
            flag_path.write_text(f"# Transcripts Fetch Flag — {expected_q_for_flag}\nUpdated: {date.today().isoformat()} (full fetch)\n", encoding="utf-8")
        elif flag_path.exists():
            print("\n── Transcripts up-to-date for this quarter — skipping (flag present) ──")
        else:
            print(f"\n── Fetching transcripts for {len(target_companies)} companies ──")
            _fetch_transcripts(target_companies)
            qdir.mkdir(parents=True, exist_ok=True)
            flag_path.write_text(f"# Transcripts Fetch Flag — {expected_q_for_flag}\nUpdated: {date.today().isoformat()}\n", encoding="utf-8")

    # Phase 2: Snapshot current state & detect file-level deltas
    print("\n── Scanning for file-level changes ──")
    current_snapshots: dict[str, dict] = {}
    for c in target_companies:
        sym = c.get("symbol", "").upper()
        current_snapshots[sym] = _snapshot_company(sym)

    # On first run, don't flag everything as "new" — just establish baseline
    if is_first_run:
        deltas: dict[str, dict] = {}
        print("  First run: establishing baseline (no file deltas flagged)")
    else:
        deltas = _compute_deltas(prev_state, current_snapshots)
        n_changes = len(deltas)
        print(f"  {n_changes} companies with file changes")
        if n_changes and n_changes <= 15:
            for sym, changes in sorted(deltas.items()):
                print(f"    {sym}: {', '.join(changes.keys())}")

    # Phase 3: Write quarterly evolving files
    expected_q = _expected_latest_quarter() or f"Mar {date.today().year}"
    holdings_map = {h["symbol"]: h for h in holdings}
    total_value = sum(h.get("current_value", 0) for h in holdings)
    for h in holdings:
        h["pct_of_portfolio"] = round(h["current_value"] / total_value * 100, 2) if total_value else 0
    # Resolve symbol aliases: if holding symbol (e.g. SONAMAC-ST) has no company dir
    # but a variant does (e.g. SONAMAC-SM), add the dir symbol as an alias in holdings_map.
    # This handles SME→mainboard platform migrations (-SM → -ST, -BE → regular, etc.)
    _PLATFORM_SUFFIXES = ("-SM", "-ST", "-BE")
    for h_sym in list(holdings_map.keys()):
        if COMPANIES_DIR.joinpath(h_sym).exists():
            continue  # direct match, no alias needed
        base = h_sym
        for sfx in _PLATFORM_SUFFIXES:
            if h_sym.endswith(sfx):
                base = h_sym[: -len(sfx)]
                break
        # Try other suffixes or bare base
        candidates = [base] + [base + s for s in _PLATFORM_SUFFIXES if base + s != h_sym]
        for cand in candidates:
            if COMPANIES_DIR.joinpath(cand).exists() and cand not in holdings_map:
                holdings_map[cand] = holdings_map[h_sym]
                break
    company_map: dict[str, dict] = {c.get("symbol", "").upper(): c for c in all_companies}

    print(f"\n── Writing quarterly files → data/earnings_season/{expected_q.replace(' ', '')}/ ──")

    # Process all existing shareholding files to detect quarter-over-quarter changes
    # even if we didn't fetch in this run
    if not sh_results:
        sh_results = {}
    for c in target_companies:
        sym = c.get("symbol", "").upper()
        if sym in sh_results:
            continue  # Already processed from fetch
        # Parse existing shareholding file and detect changes between latest two quarters
        data = _parse_existing_shareholding(sym)
        if not data:
            continue
        quarters = data.get("quarters", [])
        categories = data.get("categories", {})
        if len(quarters) < 2:
            continue  # Need at least 2 quarters to compare
        
        # Compare last two quarters within the same file
        latest_q = quarters[-1]
        prev_q = quarters[-2]
        # Process if latest quarter is the expected or newer
        # (some companies may already have next quarter data, e.g. Apr 2026 vs expected Mar 2026)
        def _q_to_tuple(q: str) -> tuple:
            _M = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
            parts = q.strip().split()
            if len(parts) == 2:
                try: return (int(parts[1]), _M.get(parts[0].lower()[:3], 0))
                except: pass
            return (0, 0)
        if _q_to_tuple(latest_q) < _q_to_tuple(expected_q):
            continue  # Skip if data is older than expected quarter
        
        KEY_CATS = ["Promoters+", "FIIs+", "DIIs+", "Public+"]
        changes: dict[str, dict] = {}
        latest: dict[str, float | None] = {}
        
        for cat in KEY_CATS:
            vals = categories.get(cat, [])
            if len(vals) < 2:
                continue
            prev_val = vals[-2]
            latest_val = vals[-1]
            latest[cat] = latest_val
            
            if prev_val is not None and latest_val is not None:
                delta = latest_val - prev_val
                if abs(delta) > 0.01:  # Significant change threshold
                    changes[cat] = {"old": prev_val, "new": latest_val, "delta": delta}
        
        if changes:
            # Detect tracked investors
            tracked_alerts = []
            seen_investors: set[str] = set()  # (section, investor_name) to avoid dupes
            individual = data.get("individual", {})
            for inv_name in tracked_names:
                for section, investors in individual.items():
                    for investor_row in investors:
                        inv_norm = _normalize_investor_name(inv_name)
                        row_norm = _normalize_investor_name(investor_row.get("name", ""))
                        if inv_norm in row_norm or row_norm in inv_norm:
                            dedup_key = (section, investor_row.get("name", ""))
                            if dedup_key in seen_investors:
                                continue
                            seen_investors.add(dedup_key)
                            values = investor_row.get("values", {})
                            # Use available quarters from individual holder data
                            # (may differ from main table quarters)
                            def _safe_float(v):
                                try:
                                    return float(v) if v and v not in ("—", "-", "") else 0.0
                                except (ValueError, TypeError):
                                    return 0.0
                            def _q_sort_key(q: str) -> tuple:
                                """Sort quarters chronologically: 'Mar 2026' -> (2026, 3)"""
                                _MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                                parts = q.strip().split()
                                if len(parts) == 2:
                                    m = _MONTHS.get(parts[0].lower()[:3], 0)
                                    try: return (int(parts[1]), m)
                                    except: pass
                                return (9999, 99)
                            avail_qs = sorted(values.keys(), key=_q_sort_key)
                            if len(avail_qs) >= 2:
                                ih_prev_q, ih_latest_q = avail_qs[-2], avail_qs[-1]
                            elif len(avail_qs) == 1:
                                ih_prev_q, ih_latest_q = None, avail_qs[0]
                            else:
                                continue
                            lv = _safe_float(values.get(ih_latest_q, ""))
                            pv = _safe_float(values.get(ih_prev_q, "")) if ih_prev_q else 0.0
                            if lv > 0 or pv > 0:
                                if lv == 0 and pv > 0:
                                    trend = "EXIT"
                                elif pv == 0 and lv > 0:
                                    trend = "NEW"
                                elif lv > pv + 0.01:
                                    trend = "UP"
                                elif lv < pv - 0.01:
                                    trend = "DOWN"
                                else:
                                    trend = "STABLE"
                                tracked_alerts.append({
                                    "investor": inv_name,
                                    "matched_name": investor_row.get("name"),
                                    "trend": trend,
                                    "latest_pct": lv,
                                    "prev_pct": pv,
                                    "latest_q": ih_latest_q,
                                })
            
            sh_results[sym] = {
                "comparison": {
                    "type": "new_quarter",
                    "new_quarter": latest_q,
                    "old_quarter": prev_q,
                    "latest_quarter": latest_q,
                    "latest": latest,
                    "changes": changes,
                },
                "tracked_alerts": tracked_alerts,
            }

    # Shareholding evolving file (shareholding changes + investor matrix)
    sh_path = _write_shareholding_quarterly(expected_q, sh_results, company_map, holdings_map)
    print(f"  ✓ {sh_path.name}")

    # Anchor investor alignment file (portfolio/{user}/anchor_alignment.md)
    if user_dir and user_dir != PORTFOLIO_DIR:
        align_path = _write_anchor_alignment(sh_results, company_map, holdings_map, user_dir.name, expected_q)
        if align_path:
            print(f"  ✓ {align_path.name}")

    # Results evolving file (per-company financial comparison tables)
    res_path = _write_results_quarterly(expected_q, all_companies, company_map, holdings_map, deltas)
    print(f"  ✓ {res_path.name}")

    # Concalls evolving file (new transcripts/PPTs)
    con_path = _write_concalls_quarterly(expected_q, deltas, company_map, holdings_map, current_snapshots)
    print(f"  ✓ {con_path.name}")

    quarter_dir = sh_path.parent
    print(f"\n✓ Quarterly files saved → {quarter_dir}/")

    # Save state
    new_state = {"last_run": today, "companies": current_snapshots}
    _save_state(new_state)
    print(f"✓ State saved → {STATE_FILE}\n")

    return quarter_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Earnings season delta report")
    parser.add_argument("--user", help="Kite user ID")
    parser.add_argument("--fetch", action="store_true",
                        help="Fetch all (shareholding + financials + transcripts). Shorthand for all 3 flags.")
    parser.add_argument("--fetch-all", action="store_true",
                        help="Full data fetch (needs Chrome CDP for financials/insights)")
    parser.add_argument("--fetch-sh", action="store_true",
                        help="Fetch shareholding only (Chrome CDP, ~7 min with parallel)")
    parser.add_argument("--fetch-fin", action="store_true",
                        help="Fetch financials only (Chrome CDP)")
    parser.add_argument("--fetch-transcripts", action="store_true",
                        help="Fetch transcripts only (no CDP needed)")
    parser.add_argument("--full-refresh", action="store_true",
                        help="Ignore previous state, treat everything as new")
    parser.add_argument("--symbols", nargs="*",
                        help="Specific symbols to scan (default: all)")
    parser.add_argument("--onboard", nargs="*",
                        help="Onboard new companies. Format: SYMBOL:Name:screener_url")
    args = parser.parse_args()

    # --fetch implies all three sub-flags
    if args.fetch:
        args.fetch_sh = True
        args.fetch_fin = True
        args.fetch_transcripts = True

    run(
        user_id=args.user,
        fetch=args.fetch,
        fetch_all=args.fetch_all,
        fetch_sh=args.fetch_sh,
        fetch_fin=args.fetch_fin,
        fetch_transcripts=args.fetch_transcripts,
        full_refresh=args.full_refresh,
        symbols=args.symbols,
        onboard=args.onboard,
    )
