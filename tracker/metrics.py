"""
Shared numbers for the order-book views and the Add/Reduce score (no LLM here):
order-book series per stock, growth over 3/6/12 months, revenue (with its source named),
share count and market cap at the latest close.
"""
from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date, timedelta

from .config import COMPANIES_DIR
from .util import to_float

KIND_RANK = {"presentation": 4, "results": 3, "press_release": 3, "order_win": 3, "capacity": 2, "acquisition": 2,
             "transcript": 2, "meet": 1, "other": 1}
MIN_VERIFIED = 0.5          # verbatim (1) or near / layout-anchored (0.5); 0 = quote not found → left out


def months_before(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) - n, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def fy_quarter(d: date) -> str:
    fy = d.year + 1 if d.month >= 4 else d.year
    q = (d.month - 4) % 12 // 3 + 1
    return f"Q{q}FY{str(fy)[2:]}"


def ob_points(conn: sqlite3.Connection, sids: list[str] | None = None, min_verified: float = MIN_VERIFIED) -> dict:
    """{sid: [point…]} oldest first, one point per as-of date (best-verified, then most authoritative source)."""
    where, args = "o.scope='total' AND o.verified >= ?", [min_verified]
    if sids:
        where += f" AND o.sid IN ({','.join('?' * len(sids))})"
        args += sids
    rows = conn.execute(
        f"SELECT o.*, d.kind, d.filed_at, d.title AS doc_title, d.url AS doc_url, d.period AS doc_period "
        f"FROM order_book_history o LEFT JOIN documents d ON d.id = o.doc_id WHERE {where}", args).fetchall()
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        p = dict(r)
        rank = (p["verified"], p["method"] == "llm", KIND_RANK.get(p["kind"] or "other", 1), p["filed_at"] or "")
        key = (p["sid"], p["as_of"])
        if key not in best or rank > best[key]["_rank"]:
            p["_rank"] = rank
            best[key] = p
    out: dict[str, list[dict]] = {}
    for (sid, _), p in best.items():
        p.pop("_rank", None)
        p["quarter"] = fy_quarter(date.fromisoformat(p["as_of"]))
        out.setdefault(sid, []).append(p)
    for pts in out.values():
        pts.sort(key=lambda p: p["as_of"])
    return out


def growth(points: list[dict], months: int) -> dict | None:
    """Latest order book vs the latest point at least `months` earlier (and not much older than that)."""
    if len(points) < 2:
        return None
    last = points[-1]
    d1 = date.fromisoformat(last["as_of"])
    newest_ok = months_before(d1, months) + timedelta(days=7)
    oldest_ok = months_before(d1, months + max(1, months // 3))   # 3M: 3–4 months back, 6M: 6–8, 1Y: 12–16
    base = None
    for p in reversed(points[:-1]):
        d0 = date.fromisoformat(p["as_of"])
        if d0 <= newest_ok:
            base = p if d0 >= oldest_ok else None
            break
    if not base or not base["value_cr"]:
        return None
    return {"pct": round((last["value_cr"] / base["value_cr"] - 1) * 100, 1), "from": base["value_cr"],
            "from_as_of": base["as_of"], "to": last["value_cr"], "to_as_of": last["as_of"]}


def quarter_bars(points: list[dict]) -> list[dict]:
    """Latest point in each FY quarter → bars for the history chart."""
    by_q: dict[str, dict] = {}
    for p in points:
        by_q[p["quarter"]] = p           # points are oldest first → the last one per quarter wins
    return sorted(by_q.values(), key=lambda p: p["as_of"])


# ── revenue, shares, market cap ─────────────────────────────────────────────

_screener_cache: dict[str, tuple[float, dict]] = {}


def screener_summary(folder: str | None) -> dict:
    """Latest Screener financials file → market cap, price, annual sales by FY, TTM sales (cached by mtime)."""
    if not folder:
        return {}
    files = sorted((COMPANIES_DIR / folder / "financials").glob("*_financials_*.txt"))
    if not files:
        return {}
    f = files[-1]
    mt = f.stat().st_mtime
    hit = _screener_cache.get(folder)
    if hit and hit[0] == mt:
        return hit[1]
    text = f.read_text(errors="replace")
    out: dict = {"file": f.name}
    m = re.search(r"_financials_(\d{4})(\d{2})(\d{2})", f.name)
    out["as_of"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    for key, rx in (("mcap_cr", r"Market Cap\s*₹?\s*([\d,\.]+)\s*Cr"), ("price", r"Current Price\s*₹?\s*([\d,\.]+)"),
                    ("pe", r"Stock P/E\s*([\d,\.]+)")):
        mm = re.search(rx, text)
        out[key] = to_float(mm.group(1)) if mm else None
    annual = text.split("## Annual Profit & Loss", 1)
    if len(annual) == 2:
        lines = [ln for ln in annual[1].split("---", 1)[0].splitlines() if "|" in ln]
        if lines:
            cols = [c.strip() for c in lines[0].split("|") if c.strip()]
            for ln in lines[1:]:
                cells = [c.strip() for c in ln.split("|")]
                label = cells[0].rstrip("+ ").strip()
                if label in ("Sales", "Revenue", "Net Profit", "EPS in Rs", "OPM %"):
                    out.setdefault("annual", {})[label] = dict(zip(cols, (to_float(c) for c in cells[1:])))
    quarterly = text.split("## Quarterly Results", 1)
    if len(quarterly) == 2:
        lines = [ln for ln in quarterly[1].split("---", 1)[0].splitlines() if "|" in ln]
        if lines:
            cols = [c.strip() for c in lines[0].split("|") if c.strip()]
            for ln in lines[1:]:
                cells = [c.strip() for c in ln.split("|")]
                label = cells[0].rstrip("+ ").strip()
                if label in ("Sales", "Revenue", "Net Profit", "EPS in Rs", "OPM %", "Operating Profit"):
                    vals = [to_float(c) for c in cells[1:1 + len(cols)]]
                    out.setdefault("quarterly", {})[label] = dict(zip(cols, vals))
    _screener_cache[folder] = (mt, out)
    return out


def revenue(conn: sqlite3.Connection, stock: sqlite3.Row | dict) -> dict:
    """Trailing-12M revenue with its source: NSE results XBRL (consolidated preferred), else Screener."""
    from . import financials

    x = financials.latest(conn, stock["sid"])
    if x.get("revenue_ttm_cr"):
        return {"value": x["revenue_ttm_cr"], "label": f"TTM to {x['period_end'][:7]} · NSE results ({x['basis']})",
                "basis": x["basis"], "source": "xbrl", "period_end": x["period_end"]}
    if stock["sales_ttm_cr"]:
        sc = screener_summary(stock["folder"])
        q = (sc.get("quarterly") or {}).get("Sales") or (sc.get("quarterly") or {}).get("Revenue") or {}
        last = list(q)[-1] if q else ""
        end = None
        m = re.match(r"([A-Za-z]{3})\s+(\d{4})", last)
        if m:
            mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(1).lower()) + 1
            end = date(int(m.group(2)), mon, calendar.monthrange(int(m.group(2)), mon)[1]).isoformat()
        return {"value": stock["sales_ttm_cr"], "label": f"TTM to {last or '—'} · Screener", "basis": None,
                "source": "screener", "period_end": end}
    return {"value": None, "label": "—", "basis": None, "source": None, "period_end": None}


def shares_cr(conn: sqlite3.Connection, stock: sqlite3.Row | dict) -> tuple[float | None, str]:
    """Shares outstanding in crore: NSE XBRL paid-up ÷ face value, else Screener market cap ÷ price."""
    from . import model
    try:
        sh = model.shares_cr(conn, stock["sid"], model.history(conn, stock))   # checked against PAT ÷ EPS
    except (KeyError, IndexError, TypeError):
        sh = None
    if sh:
        return sh, "NSE results"
    sc = screener_summary(stock["folder"])
    if sc.get("mcap_cr") and sc.get("price"):
        return sc["mcap_cr"] / sc["price"], "Screener"
    return None, ""


def market_cap(conn: sqlite3.Connection, stock: sqlite3.Row | dict, close: float | None) -> float | None:
    sh, _ = shares_cr(conn, stock)
    if sh and close:
        return round(sh * close, 1)
    return stock["mcap_cr"]
