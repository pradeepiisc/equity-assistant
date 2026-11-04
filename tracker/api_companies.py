"""
Companies API (mounted under /api/t): one row per held / watched company for the Companies page, the full model
of one company for its expanded row and its page, and writing a valuation memo on demand. Read-only apart from
the memo; no LLM except that memo.
"""
from __future__ import annotations

import yaml
from fastapi import APIRouter, HTTPException

from . import metrics, model, score
from .config import ROOT, load_settings
from .db import connect
from .util import now_ist

router = APIRouter()

SCOPES = {"portfolio": "s.in_portfolio=1", "watchlist": "s.in_watchlist=1 AND s.in_portfolio=0",
          "all": "(s.in_portfolio=1 OR s.in_watchlist=1)"}
ROW_SQL = ("SELECT s.*, t.close, t.chg_pct, t.phase, t.dma200, t.dma40, t.ret_1m, t.hi52, t.lo52, t.d AS price_d "
           "FROM stocks s LEFT JOIN technicals t USING(sid) WHERE {where} ORDER BY s.in_portfolio DESC, s.sid")

_groups: tuple[float, dict] = (0.0, {})


def _watch_groups() -> dict[str, str]:
    """sid → group name from watchlist.yaml (e.g. "Kacholia"), cached by file time."""
    global _groups
    p = ROOT / "watchlist.yaml"
    mt = p.stat().st_mtime if p.exists() else 0
    if mt != _groups[0]:
        stocks = (yaml.safe_load(p.read_text()) or {}).get("stocks", []) if p.exists() else []
        _groups = (mt, {str(w.get("symbol")): w.get("group") for w in stocks if w.get("group")})
    return _groups[1]


def _holding(conn, sid: str) -> dict | None:
    r = conn.execute("SELECT SUM(quantity) q, SUM(invested) inv, SUM(quantity * COALESCE(last_price, 0)) val, "
                     "GROUP_CONCAT(account) acc FROM holdings WHERE sid=? AND quantity > 0", (sid,)).fetchone()
    return {"qty": r["q"], "invested": r["inv"], "value": r["val"], "accounts": r["acc"]} if r and r["q"] else None


def signal(m: dict, s, tone: str | None) -> dict:
    """For a company you don't hold: is the evidence turning (results, trend, valuation)? Signals, not advice."""
    reasons, good, bad = [], 0, 0
    acts = [p for p in m["periods"] if p["sales"] is not None]
    last = acts[-1] if acts else None
    if tone == "strong":
        good += 2
        reasons.append(f"strong {last['label'] if last else ''} results".strip())
    elif tone == "weak":
        bad += 2
        reasons.append(f"weak {last['label'] if last else ''} results".strip())
    elif last and last.get("sales_yoy") is not None:
        if last["sales_yoy"] >= 15 and (last.get("pat_yoy") or 0) >= 15:
            good += 1
            reasons.append(f"{last['label']} revenue {last['sales_yoy']:+.0f}% YoY, PAT {last['pat_yoy']:+.0f}%")
        elif last["sales_yoy"] < 0:
            bad += 1
            reasons.append(f"{last['label']} revenue {last['sales_yoy']:+.0f}% YoY")
    close, d40, d200 = s["close"], s["dma40"], s["dma200"]
    if close and d40 and d200:
        if close > d40 and close > d200:
            good += 1
            reasons.append("price above DMA40 and DMA200")
        elif close < d40 and close < d200:
            bad += 1
            reasons.append("price below DMA40 and DMA200")
        elif close > d40:
            reasons.append("price back above DMA40, below DMA200")
    base = (m.get("scenarios") or {}).get("base") or {}
    if base.get("upside_pct") is not None:
        if base["upside_pct"] >= 25:
            good += 1
            reasons.append(f"base case {base['upside_pct']:+.0f}% from here")
        elif base["upside_pct"] < 0:
            bad += 1
            reasons.append(f"above the base case ({base['upside_pct']:+.0f}%)")
    if good >= 3 and bad == 0:
        return {"label": "Consider", "cls": "up", "reasons": reasons}
    if good >= 2 and bad <= 1:
        return {"label": "Improving", "cls": "ink2", "reasons": reasons}
    return {"label": "Wait", "cls": "muted", "reasons": reasons}


def _row(conn, s, settings, ob_series, groups) -> dict:
    m = model.build(conn, s, settings)
    est = m["estimates"]
    acts = [p for p in m["periods"] if p["sales"] is not None]
    last = acts[-1] if acts else None
    ev = conn.execute("SELECT title, ts, json_extract(data, '$.tone') tone FROM events WHERE sid=? AND type='results_review' "
                      "ORDER BY ts DESC LIMIT 1", (s["sid"],)).fetchone()
    held = bool(s["in_portfolio"])
    sc = score.score_stock(conn, s, settings, ob_series.get(s["sid"], [])) if held else None
    view = conn.execute("SELECT json_extract(view, '$.one_line') one_line FROM company_view WHERE sid=?", (s["sid"],)).fetchone()
    pts = ob_series.get(s["sid"], [])
    ttm = sum(p["sales"] for p in acts[-m["per_year"]:]) if len(acts) >= m["per_year"] else None
    years = est.get("years") or []
    scen = m["scenarios"] if m["scenarios"].get("available") else {}
    return {
        "sid": s["sid"], "name": s["name"], "sector": s["sector"], "held": held, "watch": bool(s["in_watchlist"]),
        "exited_on": s["exited_on"], "group": groups.get(s["sid"]), "nse": s["nse_symbol"], "bse": s["bse_code"],
        "close": s["close"], "chg_pct": s["chg_pct"], "phase": s["phase"], "ret_1m": s["ret_1m"], "price_d": s["price_d"],
        "dma40": s["dma40"], "dma200": s["dma200"],
        "holding": _holding(conn, s["sid"]) if held else None,
        "sales": [{"label": p["label"], "v": p["sales"], "est": False} for p in acts[-8:]]
                 + [{"label": p["label"], "v": p["sales"], "est": True} for p in (est.get("periods") or [])[:m["per_year"]]],
        "latest": {k: last.get(k) for k in ("label", "sales", "sales_yoy", "pat", "pat_yoy", "opm", "source")} if last else None,
        "years": [{k: y.get(k) for k in ("label", "sales", "sales_yoy", "pat", "pat_yoy", "eps", "opm")} for y in years[:2]],
        "est_source": est.get("source"),
        "scenarios": {k: (scen.get(k) or {}) for k in ("bear", "base", "bull")} if scen else None,
        "scen_fy": scen.get("fy"), "scen_source": scen.get("source", "model") if scen else None,
        "ob": {"latest": pts[-1]["value_cr"] if pts else None, "series": [p["value_cr"] for p in pts[-6:]],
               "book_to_rev": round(pts[-1]["value_cr"] / ttm, 2) if pts and ttm else None},
        "action": sc["action"] if sc else None, "score": sc["score"] if sc else None,
        "reasons": [r["text"] for r in sc["top_reasons"][:2]] if sc else [],
        "signal": None if held else signal(m, s, ev["tone"] if ev else None),
        "results_event": dict(ev) if ev else None,
        "one_line": view["one_line"] if view else None,
        "memo_at": (m.get("memo") or {}).get("created_at"),
    }


@router.get("/companies")
def companies(scope: str = "portfolio"):
    if scope not in SCOPES:
        raise HTTPException(400, "scope must be portfolio|watchlist|all")
    conn = connect()
    try:
        settings = load_settings()
        stocks = conn.execute(ROW_SQL.format(where=SCOPES[scope])).fetchall()
        series = metrics.ob_points(conn, [s["sid"] for s in stocks])
        groups = _watch_groups()
        rows = [_row(conn, s, settings, series, groups) for s in stocks]
        return {"rows": rows, "as_of": now_ist().isoformat(timespec="seconds")}
    finally:
        conn.close()


@router.get("/company/{sid}/model")
def company_model(sid: str):
    from . import valuation

    conn = connect()
    try:
        s = conn.execute(ROW_SQL.format(where="s.sid=?"), (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, f"{sid} is not tracked")
        settings = load_settings()
        m = model.build(conn, s, settings)
        m["stale"] = valuation.stale(conn, sid, settings)
        m["holding"] = _holding(conn, sid)
        m["exits"] = [dict(r) for r in conn.execute("SELECT account, exited_on, quantity, avg_price FROM exits WHERE sid=? "
                                                    "ORDER BY exited_on DESC", (sid,))]
        return m
    finally:
        conn.close()


@router.post("/company/{sid}/valuation")
def write_valuation(sid: str):
    """Write the valuation memo now (one LLM call, about a minute)."""
    from . import extract, valuation

    conn = connect()
    try:
        s = conn.execute(ROW_SQL.format(where="s.sid=?"), (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, f"{sid} is not tracked")
        settings = load_settings()
        if not extract.can_spend(conn, settings, 9000, 3500, "valuation"):
            raise HTTPException(429, "today's LLM budget is spent (tracker.yaml llm.daily_budget_inr)")
        memo = valuation.write_memo(conn, s, settings)
        conn.commit()
        if not memo:
            raise HTTPException(502, "the model returned no usable memo; nothing stored")
        return {"ok": True, "created_at": now_ist().isoformat(timespec="seconds")}
    finally:
        conn.close()
