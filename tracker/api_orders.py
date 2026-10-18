"""
Order-book dashboard API (mounted under /api/t with the rest): order books, one stock's
history, individual orders (feed) and orders per company. Read-only; no LLM.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from . import metrics
from .db import connect
from .documents import file_link
from .util import now_ist

router = APIRouter()

SCOPES = {"portfolio": "s.in_portfolio=1", "watchlist": "s.in_watchlist=1", "notes": "s.in_notes=1",
          "all": "(s.in_portfolio=1 OR s.in_watchlist=1 OR s.in_notes=1)"}


def _scope(scope: str) -> str:
    if scope not in SCOPES:
        raise HTTPException(400, "scope must be all|portfolio|watchlist|notes")
    return SCOPES[scope]


def _link(p: dict) -> str | None:
    return p.get("url") or p.get("doc_url") or file_link(p.get("path"))


def _point(p: dict) -> dict:
    return {"as_of": p["as_of"], "quarter": p["quarter"], "value_cr": p["value_cr"], "quote": p["quote"],
            "page": p["page"], "verified": p["verified"], "method": p["method"], "kind": p.get("kind"),
            "doc_id": p.get("doc_id"), "link": _link(p), "filed_at": p.get("filed_at"),
            "doc_title": p.get("doc_title")}


@router.get("/orderbooks")
def orderbooks(scope: str = "all"):
    conn = connect()
    try:
        stocks = conn.execute(f"SELECT s.*, t.close, t.d AS price_d FROM stocks s LEFT JOIN technicals t USING(sid) "
                              f"WHERE {_scope(scope)}").fetchall()
        series = metrics.ob_points(conn, [s["sid"] for s in stocks])
        rows = []
        for s in stocks:
            pts = series.get(s["sid"])
            if not pts:
                continue
            last = pts[-1]
            rev = metrics.revenue(conn, s)
            mcap = metrics.market_cap(conn, s, s["close"])
            bars = metrics.quarter_bars(pts)
            g = {f"g{m}": metrics.growth(pts, m) for m in (3, 6, 12)}
            rows.append({
                "sid": s["sid"], "name": s["name"], "nse": s["nse_symbol"], "bse": s["bse_code"], "sector": s["sector"],
                "in_portfolio": bool(s["in_portfolio"]), "in_watchlist": bool(s["in_watchlist"]),
                "order_book_cr": last["value_cr"], "as_of": last["as_of"], "verified": last["verified"],
                "updated": max((p.get("filed_at") or p.get("created_at") or "") for p in pts)[:10] or None,
                "revenue_cr": rev["value"], "revenue_label": rev["label"],
                "book_to_revenue": round(last["value_cr"] / rev["value"], 2) if rev["value"] else None,
                "mcap_cr": mcap, "points": len(pts),
                "history": [{"q": b["quarter"], "v": b["value_cr"], "as_of": b["as_of"]} for b in bars],
                **{k: (v["pct"] if v else None) for k, v in g.items()},
            })
        tracked = conn.execute(f"SELECT COUNT(*) FROM stocks s WHERE {_scope(scope)}").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM documents WHERE status IN ('queued','prepared')").fetchone()[0]
        return {"rows": rows, "tracked": tracked, "pending_docs": pending,
                "as_of": now_ist().isoformat(timespec="seconds")}
    finally:
        conn.close()


@router.get("/orderbook/{sid}")
def orderbook(sid: str):
    conn = connect()
    try:
        s = conn.execute("SELECT s.*, t.close FROM stocks s LEFT JOIN technicals t USING(sid) WHERE s.sid=?",
                         (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, f"{sid} is not tracked")
        pts = metrics.ob_points(conn, [sid]).get(sid, [])
        everything = [dict(r) for r in conn.execute(
            "SELECT o.*, d.kind, d.filed_at, d.title AS doc_title, d.url AS doc_url FROM order_book_history o "
            "LEFT JOIN documents d ON d.id=o.doc_id WHERE o.sid=? ORDER BY o.as_of DESC, o.verified DESC", (sid,))]
        used = {(p["as_of"], p.get("doc_id"), p["method"]) for p in pts}
        for e in everything:
            e["quarter"] = metrics.fy_quarter(date.fromisoformat(e["as_of"]))
        rev = metrics.revenue(conn, s)
        return {
            "sid": sid, "name": s["name"], "nse": s["nse_symbol"], "bse": s["bse_code"],
            "bars": [_point(p) for p in metrics.quarter_bars(pts)],
            "points": [_point(p) for p in pts],
            "all": [{**_point(e), "scope": e["scope"], "used": (e["as_of"], e.get("doc_id"), e["method"]) in used}
                    for e in everything],
            "growth": {str(m): metrics.growth(pts, m) for m in (3, 6, 12)},
            "latest": _point(pts[-1]) if pts else None,
            "revenue": rev,
            "book_to_revenue": round(pts[-1]["value_cr"] / rev["value"], 2) if pts and rev["value"] else None,
        }
    finally:
        conn.close()


# ── individual orders ───────────────────────────────────────────────────────

def _orders(conn, days: int, scope: str, sid: str | None = None, undated: bool = False) -> list[dict]:
    """Orders announced in the window. Undated ones (an order list inside a presentation, dated only by
    the document) are left out of period totals unless `undated`."""
    since = (now_ist().date() - timedelta(days=days)).isoformat()
    args: list = [since]
    where = [f"o.announced_on >= ?", _scope(scope)]
    if not undated:
        where.append("COALESCE(o.dated, 1) = 1")
    if sid:
        where.append("o.sid = ?")
        args.append(sid)
    rows = [dict(r) for r in conn.execute(
        f"SELECT o.*, s.name, s.nse_symbol, s.bse_code, d.kind AS doc_kind, d.title AS doc_title, d.path AS doc_path "
        f"FROM orders o JOIN stocks s ON s.sid=o.sid LEFT JOIN documents d ON d.id=o.doc_id "
        f"WHERE {' AND '.join(where)} ORDER BY o.announced_on, o.id", args)]
    llm_events = {r["event_id"] for r in rows if r["method"] == "llm" and r["event_id"]}
    kept: list[dict] = []
    for r in rows:
        if r["method"] == "filing_text" and r["event_id"] in llm_events:
            continue                          # the AI read of the same filing replaces the bare text row
        dup = next((k for k in kept if k["sid"] == r["sid"] and r["value_cr"] and k["value_cr"]
                    and abs(k["value_cr"] - r["value_cr"]) <= 0.01 * k["value_cr"]
                    and abs((date.fromisoformat(r["announced_on"]) - date.fromisoformat(k["announced_on"])).days) <= 120),
                   None)
        if dup:                               # the same order repeated in a later presentation / call
            dup.setdefault("also_in", []).append({"date": r["announced_on"], "link": r["url"], "kind": r["doc_kind"]})
            continue
        kept.append(r)
    return kept


def _order_out(r: dict, rev: dict) -> dict:
    v = r["value_cr"]
    return {
        "id": r["id"], "sid": r["sid"], "name": r["name"], "nse": r["nse_symbol"], "bse": r["bse_code"],
        "date": r["announced_on"], "customer": r["customer"], "order_type": r["order_type"], "value_cr": v,
        "duration_months": r["duration_months"], "annual_value_cr": r["annual_value_cr"],
        "revenue_pct": round(v / rev["value"] * 100, 2) if v and rev["value"] else None,
        "description": r["description"], "quote": r["quote"], "page": r["page"], "verified": r["verified"],
        "method": r["method"], "link": r["url"] or file_link(r.get("doc_path")), "kind": r.get("doc_kind"),
        "dated": r.get("dated", 1) != 0,
        "also_in": r.get("also_in", []),
    }


@router.get("/orders")
def orders_feed(days: int = 180, scope: str = "all", sid: str | None = None, undated: bool = False):
    conn = connect()
    try:
        rows = _orders(conn, days, scope, sid, undated)
        revs: dict[str, dict] = {}
        out = []
        for r in rows:
            if r["sid"] not in revs:
                revs[r["sid"]] = metrics.revenue(conn, conn.execute("SELECT * FROM stocks WHERE sid=?", (r["sid"],)).fetchone())
            out.append(_order_out(r, revs[r["sid"]]))
        out.reverse()                         # newest first
        return {"rows": out, "days": days}
    finally:
        conn.close()


@router.get("/orders/companies")
def orders_by_company(days: int = 180, scope: str = "all"):
    conn = connect()
    try:
        rows = _orders(conn, days, scope)
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["sid"], []).append(r)
        out = []
        for sid, rs in by.items():
            s = conn.execute("SELECT s.*, t.close FROM stocks s LEFT JOIN technicals t USING(sid) WHERE s.sid=?",
                             (sid,)).fetchone()
            rev = metrics.revenue(conn, s)
            items = [_order_out(r, rev) for r in rs]
            total = round(sum(i["value_cr"] or 0 for i in items), 2)
            out.append({
                "sid": sid, "name": s["name"], "nse": s["nse_symbol"], "bse": s["bse_code"],
                "orders": sorted(items, key=lambda i: i["date"], reverse=True), "count": len(items),
                "valued": sum(1 for i in items if i["value_cr"]), "total_cr": total,
                "revenue_cr": rev["value"], "revenue_label": rev["label"],
                "pct_of_revenue": round(total / rev["value"] * 100, 1) if rev["value"] and total else None,
                "mcap_cr": metrics.market_cap(conn, s, s["close"]),
                "latest": max(i["date"] for i in items),
            })
        out.sort(key=lambda r: -(r["pct_of_revenue"] or 0))
        return {"rows": out, "days": days}
    finally:
        conn.close()


@router.get("/documents")
def documents_list(sid: str | None = None, status: str | None = None, limit: int = 200):
    conn = connect()
    try:
        where, args = ["1=1"], []
        if sid:
            where.append("sid=?")
            args.append(sid)
        if status:
            where.append("status=?")
            args.append(status)
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, sid, kind, origin, url, path, title, filed_at, period, pages, status, backfill, est_tokens, "
            f"tokens_in, tokens_out, error FROM documents WHERE {' AND '.join(where)} "
            f"ORDER BY filed_at DESC LIMIT ?", (*args, limit))]
        for r in rows:
            r["link"] = r["url"] or file_link(r["path"])
        return {"rows": rows}
    finally:
        conn.close()


@router.get("/doc/{doc_id}")
def doc(doc_id: int):
    conn = connect()
    try:
        d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if d is None:
            raise HTTPException(404, "no such document")
        facts = {
            "order_book": [dict(r) for r in conn.execute("SELECT * FROM order_book_history WHERE doc_id=?", (doc_id,))],
            "orders": [dict(r) for r in conn.execute("SELECT * FROM orders WHERE doc_id=?", (doc_id,))],
            "guidance": [dict(r) for r in conn.execute("SELECT * FROM guidance WHERE doc_id=?", (doc_id,))],
            "facts": [dict(r) for r in conn.execute("SELECT * FROM doc_facts WHERE doc_id=?", (doc_id,))],
        }
        out = dict(d)
        out["hits"] = json.loads(d["hits"] or "{}")
        out["link"] = d["url"] or file_link(d["path"])
        return {"document": out, **facts}
    finally:
        conn.close()


# ── forward estimates & score ───────────────────────────────────────────────

@router.get("/scores")
def scores(scope: str = "all"):
    from . import score
    from .config import load_settings

    conn = connect()
    try:
        rows = score.compute_all(conn, load_settings(), _scope(scope).replace("s.", "s."))
        for r in rows:
            r.pop("components", None)
        return {"rows": rows, "as_of": now_ist().isoformat(timespec="seconds")}
    finally:
        conn.close()


@router.get("/score/{sid}")
def score_one(sid: str):
    from . import score
    from .config import load_settings

    conn = connect()
    try:
        s = conn.execute("SELECT s.*, t.close, t.phase, t.dma200, t.dma40 FROM stocks s LEFT JOIN technicals t USING(sid) "
                         "WHERE s.sid=?", (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, f"{sid} is not tracked")
        pts = metrics.ob_points(conn, [sid]).get(sid, [])
        out = score.score_stock(conn, s, load_settings(), pts)
        for g in out["guidance"]:
            g["link"] = g.get("url") or file_link(g.get("path"))
        out["weights"] = load_settings()["score"]
        return out
    finally:
        conn.close()
