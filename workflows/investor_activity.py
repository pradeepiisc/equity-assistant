"""
Workflow: Investor Activity Tracker
=====================================
For each stock in the target list (high-conviction holdings + watchlist),
scrapes Screener.in for:
  • Shareholding pattern — Promoter / FII / DII / Public (last 4 quarters)
  • Notable individual holders (ace investors, institutions > 1%)
  • Flags significant changes (≥1.5% shift in any category QoQ)

This is a WEEKLY / ON-DEMAND workflow (shareholding data is quarterly).
Run it once after results season or any time you want a fresh check.

Usage:
    python -m workflows.investor_activity
    python -m workflows.investor_activity --user ZV3899 --min-alloc 1.5
    python -m workflows.investor_activity --symbols SYRMA AEROFLEX DCAL
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

# ── Shared Screener session ─────────────────────────────────────────────────────
# New httpx.Client per request = no cookies = Screener tarpit (2-3 min+).
# One shared client visits homepage once to establish cookies.
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
                "Connection": "keep-alive",
            },
            follow_redirects=True,
            timeout=20,
        )
        try:
            c.get("https://www.screener.in/", timeout=10)   # establish session cookies
        except Exception:
            pass
        _SCREENER_CLIENT = c
    return _SCREENER_CLIENT

PROJECT_ROOT = Path(__file__).parent.parent
PORTFOLIO_DIR = PROJECT_ROOT / "portfolio"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FETCH_DELAY  = 1.5   # polite delay between Screener requests
CHANGE_FLAG  = 1.5   # flag if any category shifts ≥ this many percentage points QoQ
MAX_QUARTERS = 4     # how many quarters to show in table


# ── data loaders ──────────────────────────────────────────────────────────────

def _find_user_dir(user_id: str | None) -> Path:
    dirs = [d for d in PORTFOLIO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        raise FileNotFoundError("No portfolio user directories found.")
    if user_id:
        m = [d for d in dirs if d.name.upper() == user_id.upper()]
        if m:
            return m[0]
    if len(dirs) == 1:
        return dirs[0]
    raise ValueError(f"Multiple users: {[d.name for d in dirs]}. Pass --user <ID>.")


def _load_targets(user_dir: Path, min_alloc: float, extra_symbols: list[str]) -> list[dict]:
    """Return deduplicated [{symbol, name, alloc_pct}] for high-conviction + watchlist stocks."""
    targets: list[dict] = []
    seen: set[str] = set()

    # Holdings above min_alloc threshold
    holdings_dir = user_dir / "holdings"
    date_dirs = sorted((d for d in holdings_dir.iterdir() if d.is_dir()), reverse=True) if holdings_dir.exists() else []
    for d in date_dirs:
        hf = d / "holdings.json"
        if hf.exists():
            holdings = json.loads(hf.read_text())
            total = sum(h.get("current_value", h.get("last_price", 0) * h.get("quantity", 0)) for h in holdings)
            for h in holdings:
                cv = h.get("current_value", h.get("last_price", 0) * h.get("quantity", 0))
                pct = cv / total * 100 if total else 0
                sym = (h.get("tradingsymbol") or h.get("symbol", "")).upper()
                if pct >= min_alloc and sym and sym not in seen:
                    targets.append({"symbol": sym, "name": sym, "alloc_pct": round(pct, 1)})
                    seen.add(sym)
            break

    # Watchlist additions
    wl_path = PROJECT_ROOT / "watchlist.yaml"
    if wl_path.exists():
        wl = yaml.safe_load(wl_path.read_text()) or {}
        for w in wl.get("stocks", []):
            sym = w["symbol"].upper()
            if sym not in seen:
                targets.append({"symbol": sym, "name": w.get("name", sym), "alloc_pct": 0.0})
                seen.add(sym)

    # Extra symbols from CLI
    for sym in extra_symbols:
        sym = sym.upper()
        if sym not in seen:
            targets.append({"symbol": sym, "name": sym, "alloc_pct": 0.0})
            seen.add(sym)

    return sorted(targets, key=lambda x: -x["alloc_pct"])


# ── Screener.in scraper ───────────────────────────────────────────────────────

def _screener_urls(symbol: str) -> list[str]:
    """Return URLs to try in order. Standalone first — shareholding is a standalone filing."""
    slug = re.sub(r"-SM$", "", symbol, flags=re.IGNORECASE)
    return [
        f"https://www.screener.in/company/{slug}/",
        f"https://www.screener.in/company/{slug}/consolidated/",
    ]


def _latest_quarter(soup: BeautifulSoup) -> str:
    """Extract the most recent quarter header (rightmost column = newest) from the shareholding table."""
    sec = soup.find("section", id="shareholding")
    if not sec:
        return ""
    table = sec.find("table")
    if not table or not table.find("thead"):
        return ""
    ths = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    cols = [q for q in ths[1:] if q]
    return cols[-1] if cols else ""  # rightmost = most recent


def _fetch_screener_page(symbol: str) -> BeautifulSoup | None:
    """Fetch Screener.in company page; pick the URL with more recent shareholding data."""
    client = _get_screener_client()
    candidates: list[tuple[str, BeautifulSoup]] = []
    for url in _screener_urls(symbol):
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                # Only keep pages that actually have a shareholding section
                if soup.find("section", id="shareholding"):
                    candidates.append((_latest_quarter(soup), soup))
        except Exception:
            pass
    if not candidates:
        return None
    # Return the candidate whose most-recent quarter label sorts latest
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _parse_shareholding(soup: BeautifulSoup) -> dict:
    """
    Extract shareholding pattern from Screener.in page.
    Returns {quarters: [str], rows: [{name, values: [float|None]}]}
    """
    result = {"quarters": [], "rows": [], "company_id": None}

    # Find company_id for AJAX call
    tag = soup.find(attrs={"data-url": re.compile(r"/trades/company-\d+/")})
    if tag:
        m = re.search(r"/trades/company-(\d+)/", str(tag.get("data-url", "")))
        if m:
            result["company_id"] = m.group(1)

    # Find shareholding section
    sh_section = soup.find("section", id="shareholding")
    if not sh_section:
        return result

    table = sh_section.find("table")
    if not table:
        return result

    # Extract quarter headers — Screener shows oldest-first; take last MAX_QUARTERS (most recent)
    thead = table.find("thead")
    if thead:
        ths = [th.get_text(strip=True) for th in thead.find_all("th")]
        all_quarters = [q for q in ths[1:] if q]
        result["quarters"] = all_quarters[-MAX_QUARTERS:]
        result["total_quarters"] = len(all_quarters)

    total = result.get("total_quarters", MAX_QUARTERS)
    skip = max(0, total - MAX_QUARTERS)  # skip this many oldest columns

    # Extract rows — only take the most recent MAX_QUARTERS columns
    for tr in table.find("tbody").find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 2:
            continue
        name = tds[0]
        raw_vals = tds[1:]  # all data columns
        recent_vals = raw_vals[skip:skip + MAX_QUARTERS]  # most recent MAX_QUARTERS
        values = []
        for v in recent_vals:
            try:
                values.append(float(v.replace("%", "").replace(",", "").strip()))
            except ValueError:
                values.append(None)
        result["rows"].append({"name": name, "values": values})

    return result


def _fetch_ace_investors(company_id: str) -> list[dict]:
    """Fetch notable individual/institutional holders from Screener.in AJAX API."""
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    all_holders: list[dict] = []
    client = _get_screener_client()
    for cls in ["foreign_institutions", "domestic_institutions", "public"]:
        url = f"https://www.screener.in/api/3/{company_id}/investors/{cls}/quarterly/"
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for holder in data.get("investors", []):
                name  = holder.get("name", "")
                vals  = holder.get("data", [])
                latest = vals[-1] if vals else None
                prev   = vals[-2] if len(vals) >= 2 else None
                if latest and latest > 0.5:   # only notable holders >0.5%
                    all_holders.append({
                        "name": name,
                        "latest": latest,
                        "prev": prev,
                        "change": round(latest - prev, 2) if prev is not None else None,
                    })
        except Exception:
            pass
    return sorted(all_holders, key=lambda x: -(x["latest"] or 0))[:10]


# ── report builder ─────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}%"


def _delta(old: float | None, new: float | None) -> str:
    if old is None or new is None:
        return ""
    d = new - old
    if abs(d) < 0.3:
        return ""
    arrow = "▲" if d > 0 else "▼"
    return f" {arrow}{abs(d):.1f}pp"


def _build_stock_section(
    target: dict,
    sh: dict,
    ace_investors: list[dict],
) -> list[str]:
    lines: list[str] = []
    sym  = target["symbol"]
    alloc = f"  ·  {target['alloc_pct']:.1f}% of portfolio" if target["alloc_pct"] else ""

    lines.append(f"### {sym}{alloc}")

    if not sh["quarters"]:
        lines.append("*No shareholding data found on Screener.in*\n")
        return lines

    quarters = sh["quarters"]
    cat_order = ["Promoters", "FIIs", "DIIs", "Public", "No. of Shareholders"]
    rows_by_name = {r["name"]: r["values"] for r in sh["rows"]}

    # Table header
    hdr_cols = " | ".join(quarters)
    lines.append(f"| Category | {hdr_cols} | Trend |")
    lines.append("|---|" + "---|" * (len(quarters) + 1))

    # Quarters are ordered oldest→newest in Screener table; index -1 = most recent
    n_q = len(quarters)
    flags: list[str] = []
    for cat in cat_order:
        is_count = cat == "No. of Shareholders"
        # Fuzzy match category name
        matched = next(
            (v for k, v in rows_by_name.items() if cat.lower() in k.lower()),
            None,
        )
        if matched is None:
            continue

        vals = matched[:n_q]
        if is_count:
            vals_str = " | ".join(str(int(v)) if v is not None else "—" for v in vals)
        else:
            vals_str = " | ".join(_pct(v) for v in vals)

        # Latest = last column (most recent quarter), prev = one before
        latest = vals[-1] if vals else None
        prev   = vals[-2] if len(vals) >= 2 else None
        delta_str = "" if is_count else _delta(prev, latest)

        if not is_count and latest is not None and prev is not None and abs(latest - prev) >= CHANGE_FLAG:
            direction = "increased ▲" if latest > prev else "decreased ▼"
            flags.append(f"  ⚠️  **{cat}** {direction} by {abs(latest - prev):.1f}pp QoQ")

        lines.append(f"| **{cat}** | {vals_str} | {delta_str} |")

    if flags:
        lines.append("")
        lines += flags

    if ace_investors:
        lines.append("")
        lines.append("**Notable holders (>0.5%):**")
        for h in ace_investors[:5]:
            chg = f"  ({'+' if (h['change'] or 0) >= 0 else ''}{h['change']:.2f}pp QoQ)" if h.get("change") is not None else ""
            lines.append(f"  - {h['name']}: {h['latest']:.2f}%{chg}")

    lines.append("")
    return lines


def build_report(
    results: list[dict],
    snapshot_date: str,
    user_id: str,
) -> str:
    lines = [
        "# Investor Activity Report",
        f"**{user_id}  ·  {snapshot_date}**\n",
        f"*Shareholding pattern (Screener.in) for {len(results)} stocks — quarterly data*\n",
        f"> Flag threshold: ≥{CHANGE_FLAG}pp change in Promoter / FII / DII stake QoQ\n",
    ]

    alerts = [(r["target"]["symbol"], r["flags"]) for r in results if r.get("flags")]
    if alerts:
        lines.append("## ⚠️  Alert Summary")
        for sym, flags in alerts:
            for f in flags:
                lines.append(f"- **{sym}**: {f.strip()}")
        lines.append("")

    lines.append("## Shareholding Details")
    for r in results:
        lines += r["section_lines"]

    lines.append("---")
    lines.append("*Source: Screener.in  ·  Data is quarterly (updated after results)*")
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def run(
    user_id: str | None = None,
    min_alloc: float = 1.0,
    extra_symbols: list[str] | None = None,
) -> Path | None:
    user_dir = _find_user_dir(user_id)
    snapshot_date = date.today().isoformat()
    targets = _load_targets(user_dir, min_alloc, extra_symbols or [])

    print(f"\n{'='*60}")
    print(f"  INVESTOR ACTIVITY  —  {user_dir.name}  ·  {snapshot_date}")
    print(f"{'='*60}")
    print(f"  {len(targets)} stocks (alloc ≥{min_alloc}% + watchlist)\n")

    results: list[dict] = []
    for i, target in enumerate(targets, 1):
        sym = target["symbol"]
        print(f"  [{i:>2}/{len(targets)}] {sym:<16} fetching Screener...", end="", flush=True)
        soup = _fetch_screener_page(sym)
        if soup is None:
            print(" ✗ not found")
            results.append({"target": target, "section_lines": [f"### {sym}\n*Not found on Screener.in*\n"], "flags": []})
            continue

        sh = _parse_shareholding(soup)
        ace = []
        if sh.get("company_id"):
            ace = _fetch_ace_investors(sh["company_id"])

        section = _build_stock_section(target, sh, ace)
        flags = [line for line in section if line.startswith("  ⚠️")]
        print(f" ✓  {len(sh['quarters'])} quarters  |  {len(ace)} notable holders  |  {len(flags)} flags")
        results.append({"target": target, "section_lines": section, "flags": flags})
        time.sleep(FETCH_DELAY)

    report_md = build_report(results, snapshot_date, user_dir.name)
    out_dir = user_dir / "holdings" / snapshot_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "investor_activity.md"
    out_file.write_text(report_md, encoding="utf-8")
    print(f"\n✓ Investor activity report saved → {out_file}\n")
    return out_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Investor activity tracker (shareholding from Screener.in)")
    parser.add_argument("--user", help="Kite user ID (auto-detected if only one)")
    parser.add_argument("--min-alloc", type=float, default=1.0,
                        help="Min allocation %% to include a holding (default 1.0)")
    parser.add_argument("--symbols", nargs="*", default=[],
                        help="Additional specific symbols to include")
    args = parser.parse_args()
    run(user_id=args.user, min_alloc=args.min_alloc, extra_symbols=args.symbols)
