"""
Quarterly results from NSE's integrated-filing XBRL (no LLM).

The Integrated_Filing_Financials RSS item links to an XBRL instance with revenue, other income,
finance costs, D&A, PBT, PAT, EPS and paid-up capital for the quarter and the year to date,
standalone or consolidated (companies file both). EBITDA = PBT + finance costs + D&A − other income.
"""
from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from datetime import date

from .util import Http, now_ist

TAGS = {
    "RevenueFromOperations": "revenue", "OtherIncome": "other_income", "FinanceCosts": "finance",
    "DepreciationDepletionAndAmortisationExpense": "da", "ProfitBeforeTax": "pbt",
    "ProfitLossForPeriod": "pat", "ProfitOrLossAttributableToOwnersOfParent": "pat_owners",
    "BasicEarningsLossPerShareFromContinuingOperations": "eps", "BasicEarningsLossPerShare": "eps_alt",
    "PaidUpValueOfEquityShareCapital": "paid_up", "FaceValueOfEquityShareCapital": "face_value",
    # banking format
    "InterestEarned": "interest_earned", "TotalIncome": "total_income", "Income": "income",
}


def parse_results_xbrl(content: bytes) -> dict:
    root = ET.fromstring(content)
    ctx: dict[str, tuple[str | None, str | None, bool]] = {}
    for c in root:
        if not c.tag.endswith("}context"):
            continue
        start = end = None
        for el in c.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "startDate":
                start = (el.text or "").strip()
            elif tag in ("endDate", "instant"):
                end = (el.text or "").strip()
        has_dims = any(el.tag.endswith(("explicitMember", "typedMember")) for el in c.iter())
        ctx[c.get("id")] = (start, end, has_dims)
    general: dict[str, str] = {}
    periods: dict[tuple[str, str], dict] = {}
    for el in root:
        ref = el.get("contextRef")
        if not ref or ref not in ctx:
            continue
        tag = el.tag.rsplit("}", 1)[-1]
        start, end, has_dims = ctx[ref]
        val = (el.text or "").strip()
        if tag in ("Symbol", "ISIN", "NameOfTheCompany", "NatureOfReportStandaloneConsolidated", "ReportingQuarter",
                   "WhetherResultsAreAuditedOrUnaudited", "DateOfEndOfReportingPeriod"):
            general.setdefault(tag, val)
            continue
        key = TAGS.get(tag)
        if not key or has_dims or not start or not end:
            continue
        try:
            num = float(val)
        except ValueError:
            continue
        periods.setdefault((start, end), {})[key] = num
    out = []
    for (start, end), v in periods.items():
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        months = round(((d1.year - d0.year) * 12 + d1.month - d0.month + 1))
        cr = lambda k: round(v[k] / 1e7, 2) if v.get(k) is not None else None  # noqa: E731  ₹ → ₹ crore
        revenue = cr("revenue") if v.get("revenue") is not None else cr("interest_earned") or cr("total_income") or cr("income")
        ebitda = None
        if v.get("pbt") is not None:
            ebitda = round((v["pbt"] + (v.get("finance") or 0) + (v.get("da") or 0) - (v.get("other_income") or 0)) / 1e7, 2)
        pat = v.get("pat_owners") if v.get("pat_owners") is not None else v.get("pat")
        out.append({
            "period_end": end, "months": months, "revenue_cr": revenue, "other_income_cr": cr("other_income"),
            "ebitda_cr": ebitda if v.get("revenue") is not None else None, "finance_cr": cr("finance"), "da_cr": cr("da"),
            "pbt_cr": cr("pbt"), "pat_cr": round(pat / 1e7, 2) if pat is not None else None,
            "eps": v.get("eps") if v.get("eps") is not None else v.get("eps_alt"),
            "paid_up_cr": cr("paid_up"), "face_value": v.get("face_value"),
        })
    basis = "consolidated" if "consol" in general.get("NatureOfReportStandaloneConsolidated", "").lower() else "standalone"
    return {"isin": general.get("ISIN"), "symbol": general.get("Symbol"), "company": general.get("NameOfTheCompany"),
            "basis": basis, "audited": general.get("WhetherResultsAreAuditedOrUnaudited"), "periods": out}


def fetch(conn: sqlite3.Connection, http: Http, limit: int = 30) -> dict:
    """Results XBRL for every tracked stock's new Integrated_Filing_Financials feed item."""
    rows = conn.execute(
        "SELECT f.sid, f.url, f.ts FROM feed_items f WHERE f.feed='results' AND f.sid IS NOT NULL "
        "AND f.url LIKE '%.xml' AND NOT EXISTS (SELECT 1 FROM kv WHERE k = 'xbrl:' || f.url) "
        "ORDER BY f.ts DESC LIMIT ?", (limit,)).fetchall()
    stored = failed = 0
    for r in rows:
        status = "ok"
        try:
            resp = http.get(r["url"])
            if resp.status_code != 200:
                status = f"HTTP {resp.status_code}"
            else:
                parsed = parse_results_xbrl(resp.content)
                stock = conn.execute("SELECT sid FROM stocks WHERE isin=?", (parsed["isin"],)).fetchone()
                sid = stock["sid"] if stock else r["sid"]
                for p in parsed["periods"]:
                    conn.execute(
                        "INSERT OR REPLACE INTO financials_q(sid, period_end, months, basis, revenue_cr, other_income_cr, "
                        "ebitda_cr, finance_cr, da_cr, pbt_cr, pat_cr, eps, paid_up_cr, face_value, url, filed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, p["period_end"], p["months"], parsed["basis"], p["revenue_cr"], p["other_income_cr"],
                         p["ebitda_cr"], p["finance_cr"], p["da_cr"], p["pbt_cr"], p["pat_cr"], p["eps"],
                         p["paid_up_cr"], p["face_value"], r["url"], r["ts"]))
                    stored += 1
        except (ET.ParseError, ValueError) as exc:
            status = f"parse error: {exc}"
        except Exception as exc:
            status = f"error: {exc.__class__.__name__}"
        failed += status != "ok"
        conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                     (f"xbrl:{r['url']}", f'"{status}"', now_ist().isoformat(timespec="seconds")))
    return {"filings": len(rows), "periods": stored, "failed": failed}


def latest(conn: sqlite3.Connection, sid: str) -> dict:
    """Latest quarter + trailing-12M revenue/PAT from XBRL (consolidated preferred). {} when none."""
    for basis in ("consolidated", "standalone"):
        qs = conn.execute("SELECT * FROM financials_q WHERE sid=? AND basis=? AND months=3 ORDER BY period_end DESC LIMIT 8",
                          (sid, basis)).fetchall()
        if qs:
            q = dict(qs[0])
            last4 = [r for r in qs[:4]]
            if len(last4) == 4 and all(r["revenue_cr"] is not None for r in last4):
                q["revenue_ttm_cr"] = round(sum(r["revenue_cr"] for r in last4), 2)
                q["pat_ttm_cr"] = round(sum(r["pat_cr"] or 0 for r in last4), 2)
                q["eps_ttm"] = round(sum(r["eps"] or 0 for r in last4), 2)
            q["quarters"] = [dict(r) for r in qs]
            return q
    return {}
