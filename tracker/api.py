"""
Dashboard JSON API (mounted at /api/t by tracker.server).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from . import jobs, notes, research, scheduler, technicals, universe
from .config import ROOT, SEVERITY_RANK, load_settings
from .db import connect, kv_get
from .util import now_ist

router = APIRouter()

CHART_TYPES = {"cross_dma40_up", "cross_dma40_down", "cross_dma200_up", "cross_dma200_down", "golden_cross",
               "death_cross", "order_win", "fund_raising", "sast_reg29", "pledge_reg31", "results",
               "concall_transcript", "corporate_action", "note_entry_hit", "note_target_hit", "note_stop_hit"}
DEAL_KINDS = ("deal",)


def _db() -> sqlite3.Connection:
    return connect()


def _rows(cur) -> list[dict]:
    out = []
    for r in cur:
        d = dict(r)
        if "data" in d and isinstance(d["data"], str):
            try:
                d["data"] = json.loads(d["data"])
            except ValueError:
                pass
        out.append(d)
    return out


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


# ── overview ────────────────────────────────────────────────────────────────

@router.get("/summary")
def summary():
    conn = _db()
    try:
        latest = technicals.latest_trading_day(conn)
        hv = universe.holdings_value(conn)
        tech = {r["sid"]: r for r in conn.execute("SELECT * FROM technicals")}
        value = invested = day = 0.0
        stale = []
        for sid, h in hv.items():
            t = tech.get(sid)
            price = (t["close"] if t else None) or h["kite_last"] or 0
            if not t:
                stale.append(sid)
            value += h["quantity"] * price
            invested += h["invested"]
            if t and t["prev_close"]:
                day += h["quantity"] * (t["close"] - t["prev_close"])
        phases: dict[str, dict[str, int]] = {"portfolio": {}, "watchlist": {}, "notes": {}}
        above = {"dma40": 0, "dma200": 0, "n40": 0, "n200": 0}
        for s in conn.execute("SELECT s.sid, s.in_portfolio, s.in_watchlist, s.in_notes, t.phase, t.close, "
                              "t.dma40, t.dma200 FROM stocks s LEFT JOIN technicals t USING(sid)"):
            ph = s["phase"] or "No data"
            for scope in ("portfolio", "watchlist", "notes"):
                if s[f"in_{scope}"]:
                    phases[scope][ph] = phases[scope].get(ph, 0) + 1
            if s["in_portfolio"] and s["close"]:
                if s["dma40"]:
                    above["n40"] += 1
                    above["dma40"] += s["close"] > s["dma40"]
                if s["dma200"]:
                    above["n200"] += 1
                    above["dma200"] += s["close"] > s["dma200"]
        unread = {r["severity"]: r["n"] for r in conn.execute(
            "SELECT e.severity, COUNT(*) n FROM alerts a JOIN events e ON e.id=a.event_id "
            "WHERE a.read_at IS NULL GROUP BY e.severity")}
        today = now_ist().date()
        upcoming = _rows(conn.execute(
            "SELECT e.sid, s.name, e.type, e.title, e.data, e.ts, e.url FROM events e JOIN stocks s USING(sid) "
            "WHERE e.type IN ('board_meeting','corporate_action') AND e.ts >= ? ORDER BY e.ts DESC LIMIT 40",
            ((today - timedelta(days=30)).isoformat(),)))
        upcoming_out = []
        for u in upcoming:
            d = (u.get("data") or {}) if isinstance(u.get("data"), dict) else {}
            when = d.get("meeting_date") or d.get("ex_date")
            upcoming_out.append({**u, "when": when})
        jobs_last = {r["job"]: dict(r) for r in conn.execute(
            "SELECT job, MAX(finished_at) finished_at, ok, message FROM job_runs GROUP BY job")}
        return {
            "as_of": latest.isoformat() if latest else None,
            "portfolio": {"value": round(value), "invested": round(invested), "pnl": round(value - invested),
                          "pnl_pct": _pct(value, invested), "day_change": round(day),
                          "day_change_pct": round(day / (value - day) * 100, 2) if value - day else None,
                          "holdings": len(hv), "missing_prices": stale},
            "breadth": above,
            "phases": phases,
            "unread": unread,
            "upcoming": upcoming_out,
            "jobs": jobs_last,
            "live_since": kv_get(conn, "live_since"),
        }
    finally:
        conn.close()


@router.get("/stocks")
def stocks(scope: str = Query("all")):
    conn = _db()
    try:
        where = {"portfolio": "s.in_portfolio=1", "watchlist": "s.in_watchlist=1", "notes": "s.in_notes=1",
                 "all": "(s.in_portfolio=1 OR s.in_watchlist=1 OR s.in_notes=1)"}.get(scope)
        if where is None:
            raise HTTPException(400, "scope must be all|portfolio|watchlist|notes")
        hv = universe.holdings_value(conn)
        rank = research.ranking()
        rows = conn.execute(f"SELECT s.*, t.* FROM stocks s LEFT JOIN technicals t USING(sid) WHERE {where}").fetchall()
        last_event = {r["sid"]: dict(r) for r in conn.execute(
            "SELECT sid, title, type, kind, severity, MAX(ts) ts FROM events WHERE kind!='technical' AND "
            "severity!='info' AND sid IS NOT NULL GROUP BY sid")}
        last_tech = {r["sid"]: dict(r) for r in conn.execute(
            "SELECT sid, title, type, MAX(ts) ts FROM events WHERE kind='technical' GROUP BY sid")}
        stance = {r["sid"]: r["stance"] for r in conn.execute(
            "SELECT m.sid, m.stance FROM note_mentions m JOIN notes n ON n.id=m.note_id WHERE m.sid IS NOT NULL "
            "ORDER BY n.d")}
        unread = {r["sid"]: r["n"] for r in conn.execute(
            "SELECT e.sid, COUNT(*) n FROM alerts a JOIN events e ON e.id=a.event_id WHERE a.read_at IS NULL "
            "GROUP BY e.sid")}
        total = 0.0
        out = []
        for r in rows:
            h = hv.get(r["sid"])
            close = r["close"]
            value = h["quantity"] * (close or h["kite_last"] or 0) if h else None
            total += value or 0
            rk = rank.get(r["sid"]) or rank.get((r["folder"] or "").upper()) or {}
            out.append({
                "sid": r["sid"], "name": r["name"], "sector": r["sector"], "is_sme": bool(r["is_sme"]),
                "exch": r["price_exch"], "in_portfolio": bool(r["in_portfolio"]),
                "in_watchlist": bool(r["in_watchlist"]), "in_notes": bool(r["in_notes"]),
                "close": close, "chg_pct": r["chg_pct"], "ret_1w": r["ret_1w"], "ret_1m": r["ret_1m"],
                "ret_3m": r["ret_3m"], "d": r["d"],
                "dma40": r["dma40"], "dma100": r["dma100"], "dma200": r["dma200"],
                "vs40": _pct(close, r["dma40"]), "vs100": _pct(close, r["dma100"]), "vs200": _pct(close, r["dma200"]),
                "phase": r["phase"] or "No data", "phase_since": r["phase_since"],
                "from_high": _pct(close, r["hi52"]), "hi52": r["hi52"], "lo52": r["lo52"],
                "value": round(value) if value else None, "quantity": h["quantity"] if h else None,
                "avg_price": round(h["avg_price"], 2) if h and h["avg_price"] else None,
                "pnl_pct": _pct(close, h["avg_price"]) if h and h["avg_price"] else None,
                "accounts": h["accounts"] if h else [],
                "last_event": last_event.get(r["sid"]), "last_tech": last_tech.get(r["sid"]),
                "stance": stance.get(r["sid"]), "unread": unread.get(r["sid"], 0),
                "rating": rk.get("rating"), "watch_reason": r["watch_reason"],
                "mcap_cr": r["mcap_cr"], "has_research": bool(r["folder"]),
            })
        for o in out:
            o["weight"] = round(o["value"] / total * 100, 2) if o["value"] and total else None
        out.sort(key=lambda o: (-(o["value"] or 0), o["sid"]))
        return {"rows": out, "total_value": round(total)}
    finally:
        conn.close()


# ── one stock ───────────────────────────────────────────────────────────────

def _stock(conn, sid: str) -> sqlite3.Row:
    """Look up by sid, or by any alias (NSE symbol, BSE code / ticker, research folder name)."""
    s = conn.execute("SELECT * FROM stocks WHERE sid=?", (sid,)).fetchone()
    if s is None:
        key = universe.strip_suffix(sid)
        s = conn.execute(
            "SELECT * FROM stocks WHERE upper(nse_symbol)=? OR bse_code=? OR upper(bse_symbol)=? "
            "OR upper(folder)=? OR upper(folder)=? ORDER BY in_portfolio DESC LIMIT 1",
            (key, key, key, sid.upper(), key)).fetchone()
    if s is None:
        raise HTTPException(404, f"{sid} is not tracked")
    return s


def _named_holders(conn, sid: str, max_q: int = 6) -> dict:
    """Named holders per group as quarter columns, with new / exited / up / down flags."""
    from .holders import GROUP_LABEL
    from .shareholding import _tracked_investors, match_investor

    investors = _tracked_investors()
    out = {}
    for grp in ("anchor", "fii", "dii", "promoter"):
        cov = conn.execute("SELECT qdate, source FROM holder_coverage WHERE sid=? AND grp=? ORDER BY qdate DESC LIMIT ?",
                           (sid, grp, max_q)).fetchall()
        if not cov:
            continue
        qs = [c["qdate"] for c in reversed(cov)]
        sources = {c["qdate"]: c["source"] for c in cov}
        urls = {}
        hs: dict[str, dict] = {}
        for r in conn.execute(f"SELECT * FROM shareholding_holders WHERE sid=? AND grp=? AND qdate IN "
                              f"({','.join('?' * len(qs))}) ORDER BY qdate", (sid, grp, *qs)):
            h = hs.setdefault(r["nkey"], {"name": r["name"], "values": {}})
            h["values"][r["qdate"]] = r["pct"]
            if r["source"] == "nse_xbrl":
                h["name"] = r["name"].title() if r["name"].isupper() else r["name"]
            if r["url"]:
                urls[r["qdate"]] = r["url"]
        rows = []
        for k, h in hs.items():
            latest = h["values"].get(qs[-1])
            prev = h["values"].get(qs[-2]) if len(qs) > 1 else None
            if len(qs) < 2:
                status = None
            elif latest is not None and prev is None:
                status = "new"
            elif latest is None and prev is not None:
                status = "exited"
            elif latest is None:
                status = "gone"            # left before the previous quarter
            else:
                status = "up" if latest - prev >= 0.01 else "down" if prev - latest >= 0.01 else "flat"
            rows.append({**h, "latest": latest, "prev": prev, "status": status,
                         "change": round(latest - prev, 2) if latest is not None and prev is not None else None,
                         "tracked": match_investor(h["name"], investors) if grp != "promoter" else None})
        rows.sort(key=lambda r: (r["latest"] is None, -(r["latest"] or max(r["values"].values() or [0]))))
        out[grp] = {"label": GROUP_LABEL[grp], "quarters": qs, "sources": sources, "urls": urls, "rows": rows}
    return out


@router.get("/stock/{sid}")
def stock(sid: str):
    conn = _db()
    try:
        s = _stock(conn, sid)
        sid = s["sid"]
        t = conn.execute("SELECT * FROM technicals WHERE sid=?", (sid,)).fetchone()
        h = universe.holdings_value(conn).get(sid)
        events = _rows(conn.execute(
            "SELECT e.*, a.id AS alert_id, a.read_at FROM events e LEFT JOIN alerts a ON a.event_id=e.id "
            "WHERE e.sid=? ORDER BY e.ts DESC LIMIT 400", (sid,)))
        mentions = _rows(conn.execute(
            "SELECT m.*, n.title, n.source_url, n.channel, n.d FROM note_mentions m JOIN notes n ON n.id=m.note_id "
            "WHERE m.sid=? ORDER BY n.d DESC", (sid,)))
        sh = _rows(conn.execute("SELECT * FROM shareholding WHERE sid=? ORDER BY qdate", (sid,)))
        deals = _rows(conn.execute("SELECT * FROM deals WHERE sid=? ORDER BY d DESC LIMIT 100", (sid,)))
        orders = [e for e in events if e["type"] == "order_win"]
        by_q: dict[str, dict] = {}
        for e in orders:
            y, m = int(e["d"][:4]), int(e["d"][5:7])
            fy = y + 1 if m >= 4 else y
            q = {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[m]
            key = f"Q{q}FY{str(fy)[2:]}"
            b = by_q.setdefault(key, {"quarter": key, "count": 0, "value_cr": 0.0, "unvalued": 0})
            b["count"] += 1
            if e["value_cr"]:
                b["value_cr"] = round(b["value_cr"] + e["value_cr"], 2)
            else:
                b["unvalued"] += 1
        disclosed_ob = next(({"value_cr": e["data"]["order_book_cr"], "d": e["d"], "title": e["title"],
                              "url": e["url"]} for e in events
                             if isinstance(e.get("data"), dict) and e["data"].get("order_book_cr")), None)
        tech = dict(t) if t else None
        if tech:
            for w in (40, 100, 200):
                tech[f"vs{w}"] = _pct(tech["close"], tech[f"dma{w}"])
            tech["from_high"] = _pct(tech["close"], tech["hi52"])
            tech["from_low"] = _pct(tech["close"], tech["lo52"])
        holding = None
        if h:
            price = (t["close"] if t else None) or h["kite_last"]
            holding = {**h, "value": round(h["quantity"] * price) if price else None,
                       "pnl_pct": _pct(price, h["avg_price"]),
                       "pnl": round(h["quantity"] * price - h["invested"]) if price else None}
        return {
            "stock": dict(s), "technicals": tech, "holding": holding,
            "research": research.summary(s["folder"]),
            "events": events, "mentions": mentions, "shareholding": sh, "deals": deals,
            "announced_orders": sorted(by_q.values(), key=lambda b: (b["quarter"][-2:], b["quarter"][1])),
            "disclosed_order_book": disclosed_ob,
            "promoters": kv_get(conn, f"promoters:{sid}") or [],
            "holders": _named_holders(conn, sid),
            "links": {
                "screener": f"https://www.screener.in/company/{s['nse_symbol'] or s['bse_code'] or sid}/",
                "nse": f"https://www.nseindia.com/get-quotes/equity?symbol={s['nse_symbol']}" if s["nse_symbol"] else None,
                "bse": f"https://www.bseindia.com/stock-share-price/x/y/{s['bse_code']}/" if s["bse_code"] else None,
            },
        }
    finally:
        conn.close()


@router.get("/stock/{sid}/chart")
def chart(sid: str, days: int = 400):
    conn = _db()
    try:
        s = _stock(conn, sid)
        sid = s["sid"]
        series = technicals.chart_series(conn, s, load_settings()["technicals"], days)
        marks = _rows(conn.execute(
            f"SELECT d, type, kind, severity, title FROM events WHERE sid=? AND type IN "
            f"({','.join('?' * len(CHART_TYPES))}) ORDER BY ts", (sid, *CHART_TYPES)))
        return {**series, "markers": marks}
    finally:
        conn.close()


# ── feeds ───────────────────────────────────────────────────────────────────

@router.get("/events")
def events(scope: str = "all", kind: str = "", min_severity: str = "low", days: int = 30, q: str = "",
           limit: int = 300, include_untracked: bool = True):
    conn = _db()
    try:
        since = (now_ist() - timedelta(days=days)).isoformat()
        args: list = [since]
        where = ["e.ts >= ?"]
        sev = [k for k, v in SEVERITY_RANK.items() if v >= SEVERITY_RANK.get(min_severity, 1)]
        where.append(f"e.severity IN ({','.join('?' * len(sev))})")
        args += sev
        if kind:
            kinds = kind.split(",")
            where.append(f"e.kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        scope_sql = {"portfolio": "s.in_portfolio=1", "watchlist": "s.in_watchlist=1", "notes": "s.in_notes=1",
                     "all": "(s.in_portfolio=1 OR s.in_watchlist=1 OR s.in_notes=1)"}.get(scope, "1=1")
        where.append(f"({scope_sql}{' OR e.sid IS NULL' if include_untracked else ''})")
        if q:
            where.append("(e.title LIKE ? OR e.detail LIKE ? OR e.sid LIKE ? OR s.name LIKE ?)")
            args += [f"%{q}%"] * 4
        rows = _rows(conn.execute(
            f"SELECT e.*, s.name, s.in_portfolio, s.in_watchlist, s.in_notes, a.id AS alert_id, a.read_at "
            f"FROM events e LEFT JOIN stocks s ON s.sid=e.sid LEFT JOIN alerts a ON a.event_id=e.id "
            f"WHERE {' AND '.join(where)} ORDER BY e.ts DESC, e.id DESC LIMIT ?", (*args, limit)))
        return {"rows": rows}
    finally:
        conn.close()


@router.get("/alerts")
def alerts(status: str = "unread", limit: int = 200):
    conn = _db()
    try:
        cond = "a.read_at IS NULL" if status == "unread" else "1=1"
        rows = _rows(conn.execute(
            f"SELECT a.id AS alert_id, a.read_at, a.pushed_at, e.*, s.name, s.in_portfolio, s.in_watchlist, "
            f"s.in_notes FROM alerts a JOIN events e ON e.id=a.event_id LEFT JOIN stocks s ON s.sid=e.sid "
            f"WHERE {cond} ORDER BY e.ts DESC LIMIT ?", (limit,)))
        return {"rows": rows}
    finally:
        conn.close()


@router.post("/alerts/read")
def alerts_read(payload: dict = Body(...)):
    conn = _db()
    try:
        stamp = now_ist().isoformat(timespec="seconds")
        if payload.get("all"):
            conn.execute("UPDATE alerts SET read_at=? WHERE read_at IS NULL", (stamp,))
        else:
            ids = [int(i) for i in payload.get("ids", [])]
            if ids:
                conn.execute(f"UPDATE alerts SET read_at=? WHERE id IN ({','.join('?' * len(ids))})", (stamp, *ids))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/deals")
def deals(days: int = 30, include_prop: bool = False):
    conn = _db()
    try:
        since = (now_ist().date() - timedelta(days=days)).isoformat()
        cond = "" if include_prop else "AND d.client_type != 'prop'"
        rows = _rows(conn.execute(
            f"SELECT d.*, s.name, s.in_portfolio, s.in_watchlist, s.in_notes FROM deals d "
            f"LEFT JOIN stocks s ON s.sid=d.sid WHERE d.d >= ? {cond} ORDER BY d.d DESC, d.value_cr DESC",
            (since,)))
        prop = conn.execute("SELECT COUNT(*) FROM deals WHERE d >= ? AND client_type='prop'", (since,)).fetchone()[0]
        return {"rows": rows, "prop_hidden": 0 if include_prop else prop,
                "latest": kv_get(conn, "deals_latest")}
    finally:
        conn.close()


# ── notes & discovery ───────────────────────────────────────────────────────

@router.get("/notes")
def notes_list():
    conn = _db()
    try:
        ns = _rows(conn.execute("SELECT id, title, source_url, channel, d, body FROM notes ORDER BY d DESC, id DESC"))
        ms = _rows(conn.execute(
            "SELECT m.*, s.name, s.in_portfolio, s.in_watchlist, t.close, t.phase FROM note_mentions m "
            "LEFT JOIN stocks s ON s.sid=m.sid LEFT JOIN technicals t ON t.sid=m.sid"))
        by_note: dict[str, list] = {}
        for m in ms:
            by_note.setdefault(m["note_id"], []).append(m)
        for n in ns:
            n["mentions"] = by_note.get(n["id"], [])
        return {"rows": ns, "dir": str(Path("notes"))}
    finally:
        conn.close()


_job_lock = threading.Lock()
_job_state: dict = {"running": None, "last": None}


def _bg(name: str, fn) -> dict:
    if not _job_lock.acquire(blocking=False):
        return {"started": False, "running": _job_state["running"]}

    def run():
        _job_state["running"] = name
        try:
            res = fn()
            _job_state["last"] = {"job": name, "ok": res is not False, "at": now_ist().isoformat(timespec="seconds")}
        finally:
            _job_state["running"] = None
            _job_lock.release()
    threading.Thread(target=run, daemon=True).start()
    return {"started": True, "running": name}


@router.post("/notes")
def notes_create(payload: dict = Body(...)):
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    if not title or not body:
        raise HTTPException(400, "title and body are required")
    nid = notes.create(title, body, payload.get("source", ""), payload.get("channel", ""), payload.get("date"))
    conn = _db()
    try:
        before = {r["sid"] for r in conn.execute("SELECT sid FROM stocks")}
        res = notes.index(conn)
        universe.sync_universe(conn, load_settings()["accounts"])
        added = sorted({r["sid"] for r in conn.execute("SELECT sid FROM stocks")} - before)
        conn.commit()
        mentions = _rows(conn.execute("SELECT * FROM note_mentions WHERE note_id=?", (nid,)))
    finally:
        conn.close()
    if added:
        def refresh():
            with_conn = connect()
            try:
                from . import prices
                prices.replay(with_conn, added)
                cfg = load_settings()["technicals"]
                live = kv_get(with_conn, "live_since")
                for sid in added:
                    s = with_conn.execute("SELECT * FROM stocks WHERE sid=?", (sid,)).fetchone()
                    technicals.update_stock(with_conn, s, cfg, live)
                notes.check_levels(with_conn, live)
                with_conn.commit()
            finally:
                with_conn.close()
            return True
        _bg("new-stocks", refresh)
    return {"id": nid, "index": res, "added_stocks": added, "mentions": mentions}


@router.get("/discovery")
def discovery():
    conn = _db()
    try:
        ideas = _rows(conn.execute(
            "SELECT s.sid, s.name, s.sector, t.close, t.phase, t.chg_pct, t.ret_1m, t.dma40, t.dma200, "
            "GROUP_CONCAT(DISTINCT m.stance) stances, MAX(n.d) last_note, COUNT(DISTINCT m.note_id) n_notes "
            "FROM stocks s JOIN note_mentions m ON m.sid=s.sid JOIN notes n ON n.id=m.note_id "
            "LEFT JOIN technicals t ON t.sid=s.sid WHERE s.in_portfolio=0 AND s.in_watchlist=0 "
            "GROUP BY s.sid ORDER BY last_note DESC"))
        for i in ideas:
            i["vs40"], i["vs200"] = _pct(i["close"], i["dma40"]), _pct(i["close"], i["dma200"])
        since = (now_ist().date() - timedelta(days=90)).isoformat()
        investor_deals = _rows(conn.execute(
            "SELECT * FROM deals WHERE investor IS NOT NULL AND client_type='ace' AND d >= ? ORDER BY d DESC",
            (since,)))
        unresolved = _rows(conn.execute(
            "SELECT m.raw_symbol, m.text, n.title, n.id note_id FROM note_mentions m JOIN notes n ON n.id=m.note_id "
            "WHERE m.sid IS NULL"))
        return {"ideas": ideas, "investor_deals": investor_deals, "unresolved": unresolved}
    finally:
        conn.close()


@router.post("/watchlist")
def watchlist_add(payload: dict = Body(...)):
    sid = payload.get("sid")
    conn = _db()
    try:
        s = _stock(conn, sid)
        added = universe.add_to_watchlist_yaml(dict(s), payload.get("reason", ""))
        universe.sync_universe(conn, load_settings()["accounts"])
        conn.commit()
        return {"added": added}
    finally:
        conn.close()


@router.get("/search")
def search(q: str):
    conn = _db()
    try:
        like = f"%{q.strip()}%"
        rows = _rows(conn.execute(
            "SELECT exch, code, symbol, name, series FROM refdata WHERE code LIKE ? OR name LIKE ? "
            "ORDER BY CASE WHEN code = ? THEN 0 WHEN code LIKE ? THEN 1 ELSE 2 END, exch DESC LIMIT 20",
            (like, like, q.strip().upper(), f"{q.strip().upper()}%")))
        return {"rows": rows}
    finally:
        conn.close()


# ── files, status, jobs ─────────────────────────────────────────────────────

def _safe(path: str) -> Path:
    p = (ROOT / path).resolve()
    allowed = [(ROOT / "data" / "companies").resolve(), (ROOT / "portfolio").resolve(), (ROOT / "notes").resolve(),
               (ROOT / "data" / "tracker" / "filings").resolve()]
    if not any(str(p).startswith(str(a) + "/") for a in allowed) or not p.is_file():
        raise HTTPException(404, "not found")
    return p


@router.get("/report")
def report(path: str):
    p = _safe(path)
    return {"name": p.name, "content": p.read_text(errors="replace")}


@router.get("/file")
def file(path: str):
    p = _safe(path)
    return FileResponse(p)


def _scheduler_state(conn) -> dict:
    from datetime import datetime

    from .feeds import FEEDS

    sch = load_settings()["schedule"]
    now = now_ist()
    feeds = []
    for feed, fname in FEEDS.items():
        last = kv_get(conn, f"feed_polled:{feed}")
        every = jobs.feed_cadence(now, feed, sch)
        nxt = None
        if every and last:
            nxt = (datetime.fromisoformat(last) + timedelta(minutes=every)).isoformat(timespec="seconds")
        feeds.append({"feed": feed, "file": fname, "last_polled": last, "every_min": every, "next_due": nxt})
    hist = kv_get(conn, "tick_history") or []
    ld = scheduler.launchd_status()
    ld["last_tick"] = next((t["finished"] for t in reversed(hist) if t.get("source") == "launchd"), None)
    if ld["loaded"] and ld.get("interval_min"):
        since = ld["last_tick"] or ld.get("installed_at")
        grace = timedelta(minutes=ld["interval_min"] * 2 + 2)
        ld["silent"] = since is None or now - datetime.fromisoformat(since) > grace
    return {
        "server": scheduler.poller.state(),
        "launchd": ld,
        "lock": jobs.lock_holder(),
        "tick_last": kv_get(conn, "tick_last"),
        "tick_history": list(reversed(hist[-25:])),
        "feeds": feeds,
        "schedule": sch,
        "now": now.isoformat(timespec="seconds"),
    }


def _bse_state(conn) -> dict:
    from . import bse_fallback
    try:
        return bse_fallback.state(conn, load_settings())
    except Exception as exc:  # never break the status page
        return {"error": str(exc)}


def _llm_state(conn) -> dict:
    from . import documents, extract

    settings = load_settings()
    cfg = settings["llm"]
    today = extract.usage(conn)
    days = []
    for r in conn.execute("SELECT day, COUNT(*) calls, SUM(prompt_tokens) pin, SUM(completion_tokens) pout FROM llm_calls "
                          "GROUP BY day ORDER BY day DESC LIMIT 14"):
        days.append({"day": r["day"], "calls": r["calls"], "tokens": (r["pin"] or 0) + (r["pout"] or 0),
                     "inr": extract.cost_inr(r["pin"] or 0, r["pout"] or 0, settings)})
    calls = _rows(conn.execute(
        "SELECT c.*, d.kind, d.title, d.backfill FROM llm_calls c LEFT JOIN documents d ON d.id=c.doc_id "
        "ORDER BY c.id DESC LIMIT 15"))
    found = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("order_book_history", "orders", "guidance", "doc_facts", "financials_q")}
    return {
        "enabled": cfg.get("enabled", True) and load_settings()["documents"].get("enabled", True),
        "client": cfg["client"], "budget": cfg["daily_token_budget"], "backfill_share": cfg["backfill_share"],
        "today": {**today, "inr": extract.cost_inr(today["prompt_tokens"], today["completion_tokens"], settings)},
        "days": days, "calls": calls, "queue": documents.queue_counts(conn), "found": found,
        "total": {"inr": round(sum(d["inr"] for d in days), 2), "tokens": sum(d["tokens"] for d in days)},
    }


@router.get("/status")
def status():
    conn = _db()
    try:
        runs = _rows(conn.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT 40"))
        counts = {
            "stocks": conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0],
            "price_days": conn.execute("SELECT COUNT(*) FROM trading_days WHERE rows>0 AND exch='NSE'").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "feed_items": conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0],
        }
        return {"runs": runs, "counts": counts, "job": _job_state,
                "latest_day": (technicals.latest_trading_day(conn) or "").__str__(),
                "scheduler": _scheduler_state(conn), "llm": _llm_state(conn), "bse": _bse_state(conn)}
    finally:
        conn.close()


@router.get("/pulse")
def pulse():
    """Cheap poll for the open dashboard: has a tick finished since the page last loaded?"""
    conn = _db()
    try:
        last = kv_get(conn, "tick_last") or {}
        unread = conn.execute("SELECT COUNT(*) FROM alerts WHERE read_at IS NULL").fetchone()[0]
        return {"tick": last.get("finished"), "unread": unread,
                "running": jobs.lock_holder() is not None or bool(_job_state["running"]),
                "next_at": scheduler.poller.next_at}
    finally:
        conn.close()


@router.post("/launchd/{action}")
def launchd(action: str):
    if action not in ("install", "uninstall"):
        raise HTTPException(404, "action must be install|uninstall")
    return scheduler.launchd(action)


RUNNABLE = {
    "tick": lambda: jobs.tick(push=True, source="manual", force=True),
    "feeds": jobs.feeds_job,
    "deals": jobs.deals_job,
    "prices": lambda: jobs.prices_job(),
    "signals": jobs.signals_job,
    "shareholding": jobs.shareholding_job,
    "alerts": lambda: jobs.alerts_job(push=False),
    "universe": jobs.universe_job,
}


@router.post("/run/{job}")
def run(job: str):
    fn = RUNNABLE.get(job)
    if fn is None:
        raise HTTPException(404, f"unknown job {job}")
    holder = jobs.lock_holder()
    if job == "tick" and holder:
        return {"started": False, "running": f"tick ({holder.get('source', '?')})"}
    return _bg(job, fn)
