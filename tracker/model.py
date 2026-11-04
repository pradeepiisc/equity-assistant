"""
One financial model per company — the source behind the Companies table, the stock page's
"Financials & estimates" tab and the score.

  • Actuals, Screener-style: quarters (half-years for companies that report half-yearly) and fiscal
    years, from the company's Screener file with NSE results XBRL and the read results filings laid on
    top, so the latest period shows the day it is filed.
  • The company's own operating KPIs per quarter (beds, rooms, capacity, utilisation …) from the concall
    insights the research workflow extracted (data/companies/<folder>/insights/insights_values.json).
  • Estimates for the next two years, period by period: from the valuation memo when there is one
    (valuation.py — the analyst prompt), else a transparent rule: revenue from guidance → order book →
    trend, the guided or current margin, PAT at the trailing EBITDA→PAT conversion, EPS on the current
    share count, spread over periods by the company's own seasonality.
  • Bear / base / bull prices: next-fiscal-year EPS × a P/E from the stock's own P/E history and its
    sector's current P/E (or the memo's justified multiple).

No LLM here: everything is recomputed from stored data on each request.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
import statistics
from datetime import date

from .config import COMPANIES_DIR
from .util import now_ist

MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
ALIASES = {  # Screener row names (banks / NBFCs use the second set)
    "sales": ("Sales", "Revenue"), "ebitda": ("Operating Profit", "Financing Profit"), "opm": ("OPM", "Financing Margin"),
    "other_income": ("Other Income",), "pbt": ("Profit before tax",), "pat": ("Net Profit",), "eps": ("EPS in Rs",),
}
FIELDS = ("sales", "ebitda", "opm", "pat", "eps")


def fy_of(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def label_of(end: date, half: bool) -> tuple[str, int, int]:
    fy = fy_of(end)
    if half:
        idx = 1 if end.month in (7, 8, 9, 10, 11, 12) else 2
        return f"H{idx} FY{fy % 100:02d}", fy, idx
    idx = ((end.month - 4) % 12) // 3 + 1
    return f"Q{idx} FY{fy % 100:02d}", fy, idx


def period_end(fy: int, idx: int, half: bool) -> date:
    if half:
        return _end(fy - 1, 9) if idx == 1 else _end(fy, 3)
    m = (3 + 3 * idx) if idx <= 3 else 3
    return _end(fy - 1 if idx <= 3 else fy, m)


def _num(c: str) -> float | None:
    try:
        return float(str(c).replace(",", "").rstrip("%").strip())
    except ValueError:
        return None


# ── Screener file ───────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}


def _section(text: str, title: str) -> tuple[list[str], dict[str, list]]:
    part = text.split(f"## {title}", 1)
    if len(part) < 2:
        return [], {}
    lines = [ln for ln in part[1].split("\n---", 1)[0].splitlines() if "|" in ln]
    if not lines:
        return [], {}
    cols = [c.strip() for c in lines[0].split("|") if c.strip()]
    rows: dict[str, list] = {}
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split("|")]
        name = cells[0].rstrip(" +").replace(" %", "").strip()
        rows[name] = [_num(c) for c in cells[1:1 + len(cols)]]
    return cols, rows


def screener(folder: str | None) -> dict:
    """The company's latest Screener financials file, parsed (cached by file time)."""
    if not folder:
        return {}
    files = sorted((COMPANIES_DIR / folder / "financials").glob("*_financials_*.txt"))
    if not files:
        return {}
    f = files[-1]
    mt = f.stat().st_mtime
    hit = _cache.get(str(f))
    if hit and hit[0] == mt:
        return hit[1]
    text = f.read_text(errors="replace")
    out = {"file": f.name, "quarterly": _section(text, "Quarterly Results"), "annual": _section(text, "Annual Profit & Loss")}
    m = re.search(r"_financials_(\d{4})(\d{2})(\d{2})", f.name)
    out["as_of"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    for key, rx in (("mcap_cr", r"Market Cap\s*₹?\s*([\d,\.]+)\s*Cr"), ("price", r"Current Price\s*₹?\s*([\d,\.]+)"),
                    ("pe", r"Stock P/E\s*([\d,\.]+)")):
        mm = re.search(rx, text)
        out[key] = _num(mm.group(1)) if mm else None
    meta = COMPANIES_DIR / folder / "meta.json"
    try:
        url = json.loads(meta.read_text()).get("screener_url", "") if meta.exists() else ""
    except ValueError:
        url = ""
    out["basis"] = "consolidated" if "/consolidated" in (url or "") else None
    _cache[str(f)] = (mt, out)
    return out


def _row(rows: dict, field: str) -> list | None:
    for name in ALIASES[field]:
        if name in rows:
            return rows[name]
    return None


def _col_end(col: str) -> date | None:
    m = re.match(r"([A-Z][a-z]{2}) (\d{4})$", col.strip())
    if not m or m.group(1) not in MON:
        return None
    return _end(int(m.group(2)), MON[m.group(1)])


# ── actuals ─────────────────────────────────────────────────────────────────

def _blank(end: date, half: bool) -> dict:
    label, fy, idx = label_of(end, half)
    return {"key": end.isoformat()[:7], "end": end.isoformat(), "label": label, "fy": fy, "idx": idx, "est": False,
            **{f: None for f in FIELDS}, "pbt": None, "other_income": None, "source": None}


def _set(p: dict, vals: dict, source: str) -> None:
    got = False
    for k, v in vals.items():
        if v is not None:
            p[k] = v
            got = True
    if got:
        p["source"] = source
    if p["sales"] and p["ebitda"] is not None and (p["opm"] is None or source != "Screener"):
        p["opm"] = round(p["ebitda"] / p["sales"] * 100, 1)


def history(conn: sqlite3.Connection, s) -> dict:
    sc = screener(s["folder"])
    qcols, qrows = sc.get("quarterly") or ([], {})
    ends = [_col_end(c) for c in qcols]
    gaps = [(b.year * 12 + b.month) - (a.year * 12 + a.month) for a, b in zip(ends, ends[1:]) if a and b]
    recent = date(now_ist().year - 2, now_ist().month, 1).isoformat()
    has_q = conn.execute("SELECT 1 FROM financials_q WHERE sid=? AND months=3 AND period_end >= ? LIMIT 1",
                         (s["sid"], recent)).fetchone()
    has_h = conn.execute("SELECT 1 FROM financials_q WHERE sid=? AND months=6 AND period_end >= ? LIMIT 1",
                         (s["sid"], recent)).fetchone()
    if has_q:                     # quarterly filings decide (main-board companies also file half-year totals)
        half = False
    elif has_h:
        half = True
    else:                         # no XBRL: the latest Screener columns (older, pre-listing columns can be irregular)
        half = len(gaps) >= 2 and all(g == 6 for g in gaps[-2:]) or (len(gaps) == 1 and gaps[0] == 6)
    periods: dict[str, dict] = {}

    for i, end in enumerate(ends):
        if not end:
            continue
        p = periods.setdefault(end.isoformat()[:7], _blank(end, half))
        vals = {}
        for f in ("sales", "ebitda", "opm", "pat", "eps", "pbt", "other_income"):
            r = _row(qrows, f)
            vals[f] = r[i] if r and i < len(r) else None
        _set(p, vals, "Screener")

    months = 6 if half else 3
    basis = sc.get("basis")
    if not basis:
        counts = dict(conn.execute("SELECT basis, COUNT(*) FROM financials_q WHERE sid=? AND months=? GROUP BY basis",
                                   (s["sid"], months)).fetchall())
        basis = "consolidated" if counts.get("consolidated") else "standalone"
    rows = conn.execute("SELECT * FROM financials_q WHERE sid=? AND months=? AND basis=? ORDER BY period_end",
                        (s["sid"], months, basis)).fetchall() or \
        conn.execute("SELECT * FROM financials_q WHERE sid=? AND months=? ORDER BY period_end", (s["sid"], months)).fetchall()
    for r in rows:
        end = date.fromisoformat(r["period_end"])
        p = periods.setdefault(end.isoformat()[:7], _blank(end, half))
        _set(p, {"sales": r["revenue_cr"], "ebitda": r["ebitda_cr"], "pat": r["pat_cr"], "eps": r["eps"],
                 "pbt": r["pbt_cr"], "other_income": r["other_income_cr"]}, f"NSE XBRL ({r['basis']})")
    # a filing carries its own period and the year to date: the period before = year-to-date difference
    used = rows[0]["basis"] if rows else basis
    cum = {(r["period_end"], r["months"]): r for r in conn.execute(
        "SELECT * FROM financials_q WHERE sid=? AND basis=?", (s["sid"], used)).fetchall()}
    step = 6 if half else 3
    for (pe, m), r in list(cum.items()):
        if m <= step or m % step:
            continue
        end = date.fromisoformat(pe)
        prev_end = _end(end.year if end.month > step else end.year - 1, (end.month - step - 1) % 12 + 1)
        own = cum.get((pe, step))                         # this period alone
        before = cum.get((prev_end.isoformat(), m - step))  # the year to date one period earlier
        if own is None:
            continue
        if m - step == step:
            before = before or cum.get((prev_end.isoformat(), step))
        if before is not None or m - step != step:
            continue                                      # the earlier period is already known
        key = prev_end.isoformat()[:7]
        p = periods.setdefault(key, _blank(prev_end, half))
        if p["source"] and not p["source"].startswith("Screener"):
            continue
        diff = {f: (r[c] - own[c]) if r[c] is not None and own[c] is not None else None
                for f, c in (("sales", "revenue_cr"), ("ebitda", "ebitda_cr"), ("pat", "pat_cr"), ("eps", "eps"),
                             ("pbt", "pbt_cr"), ("other_income", "other_income_cr"))}
        if diff["sales"] is not None and diff["sales"] > 0:
            _set(p, {k: (round(v, 2) if v is not None else None) for k, v in diff.items()}, f"NSE XBRL ({used}, year-to-date less the period)")

    from . import results as results_mod
    for rr in conn.execute("SELECT period, numbers FROM results_reviews WHERE sid=?", (s["sid"],)).fetchall():
        nums = json.loads(rr["numbers"] or "{}")
        pdf = nums.get("pdf")
        pe = results_mod.period_end(rr["period"])
        if not pdf or not pe:
            continue
        end = date.fromisoformat(pe) if isinstance(pe, str) else pe
        p = periods.get(end.isoformat()[:7])
        if p is not None and p["sales"] is not None:
            # Screener's and the XBRL figures win; the read only fills what they left empty
            for f, k in (("pat", "pat_cr"), ("eps", "eps"), ("ebitda", "ebitda_cr")):
                if p[f] is None and pdf.get(k) is not None:
                    p[f] = pdf[k]
            if p["opm"] is None and p["ebitda"] is not None and p["sales"]:
                p["opm"] = round(p["ebitda"] / p["sales"] * 100, 1)
            continue
        if p is None:
            if half != rr["period"].startswith("H"):
                continue
            p = periods.setdefault(end.isoformat()[:7], _blank(end, half))
        _set(p, {"sales": pdf.get("revenue_cr"), "ebitda": pdf.get("ebitda_cr"), "pat": pdf.get("pat_cr"),
                 "eps": pdf.get("eps")}, "Screener (BSE)" if pdf.get("source") == "screener" else "results filing (AI read)")

    # a filing keyed in the wrong unit (100× – 10⁵× its neighbours) is dropped, not modelled
    vals = sorted(p["sales"] for p in periods.values() if p["sales"] and p["sales"] > 0)
    if len(vals) >= 3:
        med = statistics.median(vals)
        for p in periods.values():
            if p["sales"] and med > 0 and (p["sales"] / med > 30 or p["sales"] / med < 1 / 30):
                for f in ("sales", "ebitda", "opm", "pat", "eps", "pbt", "other_income"):
                    p[f] = None
                p["source"] = "dropped: out of scale with the other periods"
    plist = sorted(periods.values(), key=lambda p: p["end"])[-16:]
    by = {(p["fy"], p["idx"]): p for p in plist}
    for p in plist:
        prev = by.get((p["fy"] - 1, p["idx"]))
        p["sales_yoy"] = _pct(p["sales"], prev["sales"] if prev else None)
        p["pat_yoy"] = _pct(p["pat"], prev["pat"] if prev else None)

    acols, arows = sc.get("annual") or ([], {})
    annual: dict[int, dict] = {}
    for i, c in enumerate(acols):
        end = _col_end(c)
        if not end or end.month != 3:
            continue
        a = annual.setdefault(end.year, {"fy": end.year, "label": f"FY{end.year % 100:02d}", "est": False, "source": "Screener"})
        for f in FIELDS:
            r = _row(arows, f)
            a[f] = r[i] if r and i < len(r) else None
    per = 2 if half else 4
    for fy in sorted({p["fy"] for p in plist}):
        ps = [p for p in plist if p["fy"] == fy]
        if len(ps) == per and all(p["sales"] is not None for p in ps):
            a = annual.get(fy)
            if a is None or a.get("sales") is None:
                tot = {f: (sum(p[f] for p in ps) if all(p[f] is not None for p in ps) else None) for f in ("sales", "ebitda", "pat", "eps")}
                annual[fy] = {"fy": fy, "label": f"FY{fy % 100:02d}", "est": False, "source": "sum of periods", **tot,
                              "opm": round(tot["ebitda"] / tot["sales"] * 100, 1) if tot["ebitda"] is not None and tot["sales"] else None}
    alist = sorted(annual.values(), key=lambda a: a["fy"])[-6:]
    for i, a in enumerate(alist):
        prev = alist[i - 1] if i else None
        a["sales_yoy"] = _pct(a.get("sales"), prev.get("sales") if prev else None)
        a["pat_yoy"] = _pct(a.get("pat"), prev.get("pat") if prev else None)
    return {"half": half, "per_year": per, "basis": basis, "periods": plist, "annual": alist,
            "screener_as_of": sc.get("as_of"), "screener_pe": sc.get("pe"), "screener_mcap": sc.get("mcap_cr")}


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None


# ── operating KPIs from the concall insights ────────────────────────────────

_KPI_NUM = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(%|x\b|cr\b|crore|mn\b|million|bn\b|mw\b|gw\b|mtpa|tpa|lakh|beds?|rooms?|keys|"
                      r"units?|stores?|outlets?|sq\.?\s*ft|days?)?", re.I)


def kpis(s, limit: int = 10) -> dict:
    """{"quarters": [...oldest→newest], "metrics": [{name, cells: {quarter: {v, unit, t}}}]} — the most filled first."""
    if not s["folder"]:
        return {"quarters": [], "metrics": []}
    p = COMPANIES_DIR / s["folder"] / "insights" / "insights_values.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        data = {}
    from .results import norm_period
    qs = [q for q in (data.get("quarters") or []) if isinstance(q, str)]
    order = sorted(qs, key=lambda q: (int(m.group(2)), int(m.group(1))) if (m := re.match(r"[QH](\d) FY(\d{2})", norm_period(q) or q)) else (0, 0))
    out = []
    for name, vals in (data.get("metrics") or {}).items():
        if not isinstance(vals, dict):
            continue
        cells = {}
        for q in order:
            t = vals.get(q)
            if t in (None, "", "null"):
                continue
            t = str(t).strip()
            m = _KPI_NUM.search(t)
            cells[norm_period(q) or q] = {"v": _num(m.group(1)) if m else None, "unit": (m.group(2) or "").strip() if m else "",
                                          "t": t[:160]}
        if len(cells) >= 1:
            out.append({"name": name.strip(), "cells": cells, "filled": len(cells),
                        "numeric": sum(1 for c in cells.values() if c["v"] is not None)})
    out.sort(key=lambda k: (-k["numeric"], -k["filled"], k["name"]))
    return {"quarters": [norm_period(q) or q for q in order], "metrics": out[:limit], "file": "insights_values.json" if data else None}


# ── order book per period ───────────────────────────────────────────────────

def order_book_by_period(conn: sqlite3.Connection, sid: str, half: bool) -> dict[str, float]:
    """The latest figure within each period — the same points (metrics.ob_points) the row and the estimates use."""
    from . import metrics

    out: dict[str, float] = {}
    for p in metrics.ob_points(conn, [sid]).get(sid, []):
        try:
            d = date.fromisoformat(str(p["as_of"])[:10])
        except ValueError:
            continue
        out[label_of(d, half)[0]] = p["value_cr"]
    return out


# ── estimates ───────────────────────────────────────────────────────────────

def _mid(g: dict | None) -> float | None:
    if not g:
        return None
    lo, hi = g.get("low"), g.get("high")
    if lo is None and hi is None:
        return None
    return ((lo if lo is not None else hi) + (hi if hi is not None else lo)) / 2


def _gfy(g: dict | None) -> int | None:
    m = re.search(r"FY\s*'?(\d{2,4})", (g or {}).get("period") or "", re.I)
    if not m:
        return None
    y = int(m.group(1))
    return y + 2000 if y < 100 else y


def fy_guidance(conn: sqlite3.Connection, sid: str) -> dict[str, dict[int, dict]]:
    """Company-wide guidance for whole fiscal years only (a quarter's or a half's target is not a year's),
    the latest statement per (metric, fiscal year)."""
    from .results import norm_period

    out: dict[str, dict[int, dict]] = {}
    for g in conn.execute("SELECT * FROM guidance WHERE sid=? AND verified >= 0.5 AND (low IS NOT NULL OR high IS NOT NULL) "
                          "AND COALESCE(scope, 'total')='total' ORDER BY stated_on, id", (sid,)):
        p = norm_period(g["period"])
        if p and re.fullmatch(r"FY\d{2}", p):
            out.setdefault(g["metric"], {})[2000 + int(p[2:])] = dict(g)
    return out


def shares_cr(conn: sqlite3.Connection, sid: str, h: dict) -> float | None:
    """Shares outstanding, in crore: paid-up capital ÷ face value from a recent XBRL filing that agrees (±20%) with
    the reported PAT ÷ EPS — filers sometimes key capital in the wrong unit — else PAT ÷ EPS itself."""
    ratios = [p["pat"] / p["eps"] for p in h["periods"][-6:] if p["pat"] and p["eps"] and p["pat"] > 0.5 and p["eps"] > 0]
    ref = ratios[-1] if ratios else None           # the latest period's: IPOs, bonuses and splits change the count
    cands = [r["paid_up_cr"] / r["face_value"] for r in conn.execute(
        "SELECT paid_up_cr, face_value FROM financials_q WHERE sid=? AND paid_up_cr > 0 AND face_value BETWEEN 0.1 AND 1000 "
        "ORDER BY period_end DESC LIMIT 6", (sid,))]
    for sh in cands:
        if ref is None or abs(sh / ref - 1) <= 0.25:
            return round(sh, 4)
    return round(ref, 4) if ref else None


def estimates(conn: sqlite3.Connection, s, h: dict, settings: dict) -> dict:
    """Next two years, per period and per fiscal year — rule-based (see the module docstring)."""
    from . import metrics, score

    per, half = h["per_year"], h["half"]
    acts = [p for p in h["periods"] if p["sales"] is not None]
    if len(acts) < per:
        return {"available": False, "why": "fewer than a year of reported periods"}
    last = acts[-1]
    fy_cur = last["fy"] if last["idx"] < per else last["fy"] + 1
    ttm = acts[-per:]
    ttm_sales = sum(p["sales"] for p in ttm)
    ttm_ebitda = sum(p["ebitda"] for p in ttm) if all(p["ebitda"] is not None for p in ttm) else None
    ttm_pat = sum(p["pat"] for p in ttm) if all(p["pat"] is not None for p in ttm) else None
    prev_ttm = acts[-2 * per:-per] if len(acts) >= 2 * per else []
    trend = _pct(ttm_sales, sum(p["sales"] for p in prev_ttm)) if prev_ttm else None
    annual = {a["fy"]: a for a in h["annual"]}
    by = {(p["fy"], p["idx"]): p for p in acts}

    def fy_sales(fy):
        a = annual.get(fy)
        if a and a.get("sales"):
            return a["sales"]
        ps = [by.get((fy, i)) for i in range(1, per + 1)]
        return sum(p["sales"] for p in ps) if all(ps) else None

    # seasonality: each period's share of its fiscal year, averaged over the last two complete years
    shares = []
    for fy in (fy_cur - 1, fy_cur - 2):
        ps = [by.get((fy, i)) for i in range(1, per + 1)]
        if all(ps):
            tot = sum(p["sales"] for p in ps)
            if tot > 0:
                shares.append([p["sales"] / tot for p in ps])
    weights = [sum(x[i] for x in shares) / len(shares) for i in range(per)] if shares else [1 / per] * per

    fyg = fy_guidance(conn, s["sid"])
    notes: list[str] = []
    base_prev = fy_sales(fy_cur - 1) or ttm_sales

    def guided_revenue(fy, prior):
        g = (fyg.get("revenue") or {}).get(fy)
        if g and (g.get("unit") in ("cr", None)) and _mid(g) and (not prior or 0.5 <= _mid(g) / prior <= 2.5):
            return _mid(g), f"guided revenue ₹{_mid(g):,.0f} Cr for FY{fy % 100:02d}"
        g = (fyg.get("revenue_growth") or {}).get(fy)
        if g and _mid(g) is not None and prior and -50 < _mid(g) < 300:
            return prior * (1 + _mid(g) / 100), f"guided {_mid(g):g}% growth for FY{fy % 100:02d}"
        return None, None

    pts = metrics.ob_points(conn, [s["sid"]]).get(s["sid"], [])
    book = pts[-1]["value_cr"] if pts else None
    ex = conn.execute("SELECT value FROM doc_facts WHERE sid=? AND section='execution' AND verified >= 0.5 "
                      "ORDER BY id DESC LIMIT 1", (s["sid"],)).fetchone()
    months = ex["value"] if ex and ex["value"] and 3 <= ex["value"] <= 120 else settings["score"]["default_execution_months"]
    book_yearly = book / max(1.0, months / 12) if book else None
    order_driven = bool(book and ttm_sales and book / ttm_sales >= 0.5)

    def trend_growth():
        return max(-20.0, min(50.0, trend if trend is not None else 10.0))

    cur, why = guided_revenue(fy_cur, base_prev)
    if cur is None and order_driven:
        reported = sum(by[(fy_cur, i)]["sales"] for i in range(1, per + 1) if (fy_cur, i) in by)
        left = per - sum(1 for i in range(1, per + 1) if (fy_cur, i) in by)
        cur = reported + book_yearly * left / per
        cur = max(cur, base_prev * (1 + min(trend_growth(), 0) / 100))
        stated = bool(ex and ex["value"] == months)
        # an assumed execution period can't drive growth far beyond the company's own trend
        cap = 1.7 if stated else 1 + max(0.15, min(0.40, (trend or 0) / 100 + 0.10))
        why = f"order book ₹{book:,.0f} Cr over {months:g} months ({'stated' if stated else 'assumed'})"
        if cur > base_prev * cap:
            cur = base_prev * cap
            why += f", capped at {cap - 1:+.0%} growth"
        why += f" for the remaining {left} period(s)"
    if cur is None:
        cur, why = base_prev * (1 + trend_growth() / 100), f"trailing growth {trend_growth():+.0f}% carried forward"
    notes.append(f"FY{fy_cur % 100:02d}E revenue: {why}")

    nxt, why = guided_revenue(fy_cur + 1, cur)
    if nxt is None:
        g = (cur / base_prev - 1) * 100 if base_prev else trend_growth()
        g = max(-10.0, min(35.0, g * 0.75))
        if order_driven and book_yearly and book_yearly > cur * (1 + g / 100):
            stated_ex = bool(ex and ex["value"] == months)
            limit = 40.0 if stated_ex else max(15.0, min(30.0, (trend or 0) + 10))
            g = max(g, min(limit, (book_yearly / cur - 1) * 100))
            why = f"order book executes ₹{book_yearly:,.0f} Cr a year ({'stated' if stated_ex else 'assumed'} period, ≤{limit:.0f}%)"
        else:
            why = f"growth tapered to {g:+.0f}%"
        nxt = cur * (1 + g / 100)
    notes.append(f"FY{(fy_cur + 1) % 100:02d}E revenue: {why}")
    g2 = max(-10.0, min(30.0, ((nxt / cur - 1) * 100) * 0.8)) if cur else 10.0
    yr3 = nxt * (1 + g2 / 100)
    notes.append(f"FY{(fy_cur + 2) % 100:02d}E revenue: growth {g2:+.0f}% (tapered)")

    opm_ttm = round(ttm_ebitda / ttm_sales * 100, 1) if ttm_ebitda is not None and ttm_sales else None
    recent = acts[-2 * per:]
    opms = [p["ebitda"] / p["sales"] * 100 for p in recent if p["ebitda"] is not None and p["sales"]]
    if len(opms) >= per and opm_ttm is not None:
        med = statistics.median(opms)
        if abs(opm_ttm - med) > 5:                # a one-off period (exceptional gain or charge) distorts the sum
            notes.append(f"Trailing margin {opm_ttm}% skewed by a one-off period; using the typical {med:.1f}%")
            opm_ttm = round(med, 1)
    # a margin target counts only for the year it names, stepped in from the trailing margin; a target far from
    # where the company is (or in the wrong unit) is left out
    targets = sorted((y, _mid(g)) for y, g in (fyg.get("ebitda_margin") or {}).items()
                     if y >= fy_cur and _mid(g) is not None and 0 < _mid(g) < 80 and (g.get("unit") in ("pct", "%", None))
                     and (opm_ttm is None or abs(_mid(g) - opm_ttm) <= 15))

    def margin_for(y):
        if not targets:
            return opm_ttm
        for t, gm in targets:
            if y <= t:
                if opm_ttm is None or y == t:
                    return gm
                return opm_ttm + (gm - opm_ttm) * (y - (fy_cur - 1)) / (t - (fy_cur - 1))
        return targets[-1][1]
    margin = {y: margin_for(y) for y in (fy_cur, fy_cur + 1, fy_cur + 2)}
    if targets:
        notes.append(f"EBITDA margin: guided {targets[0][1]:g}% for FY{targets[0][0] % 100:02d}, stepped in from the "
                     f"trailing {opm_ttm}%")
    elif opm_ttm is not None:
        notes.append(f"EBITDA margin: trailing {opm_ttm}%")
    convs = [p["pat"] / p["ebitda"] for p in recent if p["pat"] is not None and p["ebitda"] and p["ebitda"] > 0]
    conv = None
    if convs and len(convs) >= per:
        conv = max(0.05, min(1.0, statistics.median(convs)))
    elif ttm_ebitda and ttm_ebitda > 0 and ttm_pat is not None:
        conv = max(0.05, min(1.0, ttm_pat / ttm_ebitda))
    pms = [p["pat"] / p["sales"] for p in recent if p["pat"] is not None and p["sales"]]
    pat_margin = statistics.median(pms) if len(pms) >= per else (ttm_pat / ttm_sales if ttm_pat is not None and ttm_sales else None)
    sh = shares_cr(conn, s["sid"], h)

    def pnl(sales, m):
        ebitda = sales * m / 100 if m is not None else None
        pat = ebitda * conv if ebitda is not None and conv is not None else (sales * pat_margin if pat_margin is not None else None)
        eps = pat / sh if pat is not None and sh else None
        return {"sales": round(sales, 1), "ebitda": round(ebitda, 1) if ebitda is not None else None,
                "opm": round(m, 1) if m is not None else None, "pat": round(pat, 1) if pat is not None else None,
                "eps": round(eps, 2) if eps is not None else None}

    # the year so far vs the same periods a year ago: when it runs ahead of the guided / modelled year,
    # the rest of the year follows the run-rate, not a guidance that is already being beaten
    done_p = [by[(fy_cur, i)] for i in range(1, per + 1) if (fy_cur, i) in by]
    same_p = [by.get((fy_cur - 1, p["idx"])) for p in done_p]
    ytd = _pct(sum(p["sales"] for p in done_p), sum(p["sales"] for p in same_p)) if done_p and all(same_p) else None
    ytd = max(-30.0, min(80.0, ytd)) if ytd is not None else None
    fy_tot = {fy_cur: cur, fy_cur + 1: nxt, fy_cur + 2: yr3}
    periods = []
    fy, idx = last["fy"], last["idx"]
    while len(periods) < 2 * per:
        idx += 1
        if idx > per:
            fy, idx = fy + 1, 1
        if fy not in fy_tot:
            break
        if fy == fy_cur:      # what is left of the current year, in the usual proportions
            done = sum(by[(fy, i)]["sales"] for i in range(1, per + 1) if (fy, i) in by)
            left_w = sum(weights[i - 1] for i in range(1, per + 1) if (fy, i) not in by) or 1
            sales = max(0.0, fy_tot[fy] - done) * weights[idx - 1] / left_w
            prev = by.get((fy - 1, idx))
            floor = prev["sales"] * (1 + (ytd if ytd is not None else trend_growth()) * 0.8 / 100) if prev else 0
            if sales < floor:                                 # the year is running ahead of the modelled total
                sales = floor
                if "run-rate" not in notes[0]:
                    notes[0] += f" — raised to the run-rate: the year so far is {ytd:+.0f}% YoY" if ytd is not None else ""
        else:
            sales = fy_tot[fy] * weights[idx - 1]
        end = period_end(fy, idx, half)
        p = _blank(end, half) | pnl(sales, margin.get(fy)) | {"est": True, "source": "model"}
        prev = by.get((fy - 1, idx)) or next((q for q in periods if q["fy"] == fy - 1 and q["idx"] == idx), None)
        p["sales_yoy"] = _pct(p["sales"], prev["sales"] if prev else None)
        p["pat_yoy"] = _pct(p["pat"], prev["pat"] if prev else None)
        periods.append(p)
    proj_cur = sum(p["sales"] for p in periods if p["fy"] == fy_cur)
    if periods and any(p["fy"] == fy_cur for p in periods):
        new_cur = sum(p["sales"] for p in done_p) + proj_cur
        if new_cur > fy_tot[fy_cur] * 1.01 and fy_tot[fy_cur + 1] <= new_cur * 1.05:
            fy_tot[fy_cur + 1] = new_cur * (fy_tot[fy_cur + 1] / fy_tot[fy_cur])      # keep the next year's growth
            fy_tot[fy_cur + 2] = fy_tot[fy_cur + 1] * (fy_tot[fy_cur + 2] / nxt)
            nxt_scale = fy_tot[fy_cur + 1] / nxt
            for p in periods:
                if p["fy"] > fy_cur:
                    p.update(pnl(p["sales"] * (fy_tot[p["fy"]] / {fy_cur + 1: nxt, fy_cur + 2: yr3}[p["fy"]]), margin.get(p["fy"])))
        fy_tot[fy_cur] = new_cur
    for p in periods:             # YoY on the final figures (after any rescale)
        prev = by.get((p["fy"] - 1, p["idx"])) or next((q for q in periods if q["fy"] == p["fy"] - 1 and q["idx"] == p["idx"]), None)
        p["sales_yoy"] = _pct(p["sales"], prev["sales"] if prev else None)
        p["pat_yoy"] = _pct(p["pat"], prev["pat"] if prev else None)
    years = []
    prev_a = annual.get(fy_cur - 1)
    for y in (fy_cur, fy_cur + 1, fy_cur + 2):
        a = {"fy": y, "label": f"FY{y % 100:02d}E", "est": True, "source": "model"} | pnl(fy_tot[y], margin.get(y))
        a["sales_yoy"] = _pct(a["sales"], (prev_a or {}).get("sales"))
        a["pat_yoy"] = _pct(a["pat"], (prev_a or {}).get("pat"))
        years.append(a)
        prev_a = a
    return {"available": True, "source": "model", "periods": periods, "years": years, "notes": notes,
            "shares_cr": sh, "order_book_cr": book, "execution_months": months, "trend_pct": trend,
            "opm_ttm": opm_ttm, "conversion": round(conv, 2) if conv is not None else None}


# ── P/E and scenarios ───────────────────────────────────────────────────────

def pe_band(conn: sqlite3.Connection, s, h: dict) -> dict:
    """The stock's trailing P/E at each period end over the last three years."""
    per = h["per_year"]
    acts = [p for p in h["periods"] if p["eps"] is not None]
    vals = []
    for i in range(per - 1, len(acts)):
        win = acts[i - per + 1:i + 1]
        if len(win) < per:
            continue
        eps = sum(p["eps"] for p in win)
        if eps <= 0:
            continue
        exch, code = ("NSE", s["nse_symbol"]) if s["nse_symbol"] else ("BSE", s["bse_code"])
        r = conn.execute("SELECT close FROM prices WHERE exch=? AND code=? AND d <= ? ORDER BY d DESC LIMIT 1",
                         (exch, code, acts[i]["end"])).fetchone()
        if r and r["close"]:
            vals.append(r["close"] / eps)
    vals = [v for v in vals[-12:] if 0 < v < 300]
    if len(vals) < 3:
        return {"n": len(vals)}
    vals.sort()
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [vals[0], statistics.median(vals), vals[-1]]
    return {"n": len(vals), "median": round(statistics.median(vals), 1), "p25": round(q[0], 1), "p75": round(q[-1], 1)}


_sector_cache: dict[str, tuple[float, dict]] = {}


def sector_pe(conn: sqlite3.Connection, sector: str | None) -> dict:
    """Median current P/E of the tracked companies in the same sector (Screener's P/E), with the peer count."""
    if not sector:
        return {"n": 0}
    import time
    hit = _sector_cache.get(sector)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    head = sector.split(" - ")[0].strip().lower()
    vals = []
    for r in conn.execute("SELECT folder, sector FROM stocks WHERE folder IS NOT NULL AND sector IS NOT NULL").fetchall():
        if r["sector"].split(" - ")[0].strip().lower() != head:
            continue
        pe = screener(r["folder"]).get("pe")
        if pe and 0 < pe < 300:
            vals.append(pe)
    out = {"n": len(vals), "median": round(statistics.median(vals), 1) if len(vals) >= 3 else None, "group": head}
    _sector_cache[sector] = (time.time(), out)
    return out


def trailing_eps(h: dict, shares: float | None) -> float | None:
    """The last year's PAT ÷ today's share count (summed period EPS breaks on one-offs, gaps and share changes)."""
    per = h["per_year"]
    acts = [p for p in h["periods"] if p["sales"] is not None]
    if len(acts) < per:
        return None
    if shares and all(p["pat"] is not None for p in acts[-per:]):
        return sum(p["pat"] for p in acts[-per:]) / shares
    eps = [p["eps"] for p in acts[-per:]]
    return sum(eps) if all(e is not None for e in eps) else None


def current_pe(h: dict, close: float | None, shares: float | None = None) -> float | None:
    """Price ÷ trailing EPS, when both are meaningful."""
    eps = trailing_eps(h, shares)
    pe = close / eps if close and eps and eps > 0 else None
    return round(pe, 1) if pe and 2 < pe < 300 else None


def scenarios(conn: sqlite3.Connection, s, h: dict, est: dict, close: float | None) -> dict:
    if not est.get("available") or len(est["years"]) < 2:
        return {"available": False}
    yr = est["years"][1]                       # next fiscal year: 12–24 months out
    own, sec = pe_band(conn, s, h), sector_pe(conn, s["sector"])
    sh = est.get("shares_cr")
    cur = current_pe(h, close, sh)
    picks = [v for v in (cur, own.get("median"), sec.get("median")) if v]
    base_pe = statistics.median(picks) if picks else 20.0
    if cur:                                    # a re-rating within reason of where the market prices it today
        base_pe = max(cur * 0.8, min(cur * 1.25, base_pe))
    base_pe = max(6.0, min(80.0, base_pe))
    bear_pe = max(base_pe * 0.6, min(base_pe * 0.85, own.get("p25") or base_pe * 0.8))
    bull_pe = min(base_pe * 1.5, max(base_pe * 1.15, own.get("p75") or base_pe * 1.2))
    if cur:
        bull_pe = min(bull_pe, cur * 1.6)
    t_eps = trailing_eps(h, sh)
    conv = est.get("conversion")
    named = [k for k, v in (("today's P/E", cur), ("own 3-year P/E", own.get("median")),
                            (f"sector P/E ({sec.get('n')} peers)", sec.get("median"))) if v]
    out = {"available": True, "fy": yr["label"], "pe_basis": {"own": own, "sector": sec, "current": cur,
           "base": ("median of " + ", ".join(named) + (" (within 0.75–1.5× today's)" if cur else "")) if named else "default 20×"}}
    out["trailing_eps"] = round(t_eps, 2) if t_eps else None
    if not yr.get("eps") or yr["eps"] <= 0 or (close and close / yr["eps"] > 150):
        out["available"] = False
        out["why"] = "earnings too small or negative for a P/E valuation"
        return out
    for name, rev_f, m_d, pe in (("bear", 0.88, -1.5, bear_pe), ("base", 1.0, 0.0, base_pe), ("bull", 1.08, 1.0, bull_pe)):
        sales = yr["sales"] * rev_f
        m = (yr["opm"] + m_d) if yr["opm"] is not None else None
        if m is not None and conv is not None:
            pat = sales * m / 100 * conv
        elif yr["pat"] is not None:
            pat = yr["pat"] * rev_f
        else:
            pat = None
        eps = pat / sh if pat is not None and sh else None
        if eps is not None and t_eps and t_eps > 0 and eps > 3 * t_eps:
            eps = 3 * t_eps                    # at most three times trailing EPS within two years
            out.setdefault("notes", []).append(f"{name}: EPS capped at 3× trailing")
        price = eps * pe if eps is not None and eps > 0 else None
        out[name] = {"revenue": round(sales, 1), "margin": round(m, 1) if m is not None else None,
                     "pat": round(pat, 1) if pat is not None else None, "eps": round(eps, 2) if eps is not None else None,
                     "pe": round(pe, 1), "price": round(price, 1) if price else None,
                     "upside_pct": round((price / close - 1) * 100, 1) if price and close else None}
    return out


# ── the whole model ─────────────────────────────────────────────────────────

def build(conn: sqlite3.Connection, s, settings: dict) -> dict:
    """Actuals, KPIs, order book, estimates and scenarios for one stock row (stocks ⋈ technicals)."""
    from . import valuation

    h = history(conn, s)
    close = s["close"] if "close" in s.keys() else None
    est = estimates(conn, s, h, settings)
    memo = valuation.latest(conn, s["sid"])
    if memo and est.get("available"):
        est = valuation.apply(memo, est, h)
    rule = scenarios(conn, s, h, est, close)
    sc = valuation.scenarios_from(memo, est, close, rule) if memo else None
    sc = sc or rule
    if sc is not rule and rule.get("available"):
        sc["rule"] = {k: rule.get(k) for k in ("bear", "base", "bull", "pe_basis")}   # the rule-based cases, for comparison
    return {"sid": s["sid"], "half": h["half"], "per_year": h["per_year"], "basis": h["basis"],
            "periods": h["periods"], "annual": h["annual"], "screener_as_of": h["screener_as_of"],
            "estimates": est, "scenarios": sc, "kpis": kpis(s), "order_book": order_book_by_period(conn, s["sid"], h["half"]),
            "close": close, "memo": valuation.public(memo) if memo else None,
            "as_of": now_ist().isoformat(timespec="seconds")}
