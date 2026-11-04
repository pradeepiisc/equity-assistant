"""
Forward estimates and a transparent Add / Hold / Trim / Exit / Watch score per stock (no LLM).

Inputs, all already in the tracker: verified order-book history and guidance (P2), NSE results
XBRL and Screener financials, technical phase, shareholding (promoter / FII / DII), named-holder
and tracked-investor moves, pledge filings, and the fair-value range from existing research.

Every component returns points in [-2, +2] and a plain-English reason; the score is the
weighted sum (weights and thresholds under `score:` in tracker.yaml). Components without data
are skipped and listed, so a thin score is visible as thin. Decision support, not an order.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

from . import metrics
from .config import COMPANIES_DIR
from .util import now_ist, to_float

PHASE_POINTS = {"Strong Bullish": 1.5, "Bullish Pullback": 0.5, "Neutral": 0.0, "Bearish Rally": -0.5, "Bearish": -1.5}
NOTE_ACTION = {"add": "Add", "reduce": "Trim", "exit": "Exit", "watch": "Watch", "hold": "Hold", "avoid": "Exit"}


def _fy(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _clip(v: float, lo: float = -2, hi: float = 2) -> float:
    return max(lo, min(hi, v))


# ── inputs ──────────────────────────────────────────────────────────────────

_json_cache: dict[str, tuple[float, dict | None]] = {}


def _report(folder: str | None, name: str) -> dict | None:
    if not folder:
        return None
    p = COMPANIES_DIR / folder / "reports" / f"{name}.json"
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    hit = _json_cache.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        data = json.loads(p.read_text(errors="replace"))
        data = data if isinstance(data, dict) else None
    except ValueError:
        data = None
    _json_cache[str(p)] = (mt, data)
    return data


def _ttm(vals: list[float | None], n: int = 4) -> float | None:
    v = [x for x in vals[-n:] if x is not None]
    return sum(v) if len(v) == n else None


def quarterly(conn: sqlite3.Connection, s) -> dict:
    """Sales / OPM / EPS / PAT per reported period, oldest first: the merged history of model.py (Screener's file
    with NSE XBRL and the read results filings on top), so the latest results count the day they are filed."""
    from . import model

    h = model.history(conn, s)
    ps = [p for p in h["periods"] if p["sales"] is not None]
    if ps:
        srcs = sorted({p["source"].split(" (")[0] for p in ps[-h["per_year"]:] if p["source"]})
        return {"source": " + ".join(srcs), "labels": [p["label"] for p in ps], "sales": [p["sales"] for p in ps],
                "eps": [p["eps"] for p in ps], "pat": [p["pat"] for p in ps], "opm": [p["opm"] for p in ps],
                "n": h["per_year"], "latest": ps[-1]["label"]}
    for basis in ("consolidated", "standalone"):
        rows = conn.execute("SELECT * FROM financials_q WHERE sid=? AND basis=? AND months=3 ORDER BY period_end",
                            (s["sid"], basis)).fetchall()
        if len(rows) >= 8:
            return {"source": f"NSE results ({basis})", "labels": [r["period_end"][:7] for r in rows],
                    "sales": [r["revenue_cr"] for r in rows], "eps": [r["eps"] for r in rows],
                    "pat": [r["pat_cr"] for r in rows],
                    "opm": [round(r["ebitda_cr"] / r["revenue_cr"] * 100, 2) if r["ebitda_cr"] is not None and r["revenue_cr"]
                            else None for r in rows]}
    sc = metrics.screener_summary(s["folder"])
    q = sc.get("quarterly") or {}
    sales = q.get("Sales") or q.get("Revenue") or {}
    if not sales:
        return {}
    labels = list(sales)
    return {"source": "Screener", "labels": labels, "sales": list(sales.values()),
            "eps": [(q.get("EPS in Rs") or {}).get(k) for k in labels],
            "pat": [(q.get("Net Profit") or {}).get(k) for k in labels],
            "opm": [(q.get("OPM %") or {}).get(k) for k in labels]}


def _guidance(conn: sqlite3.Connection, sid: str) -> dict:
    """Latest verified guidance per metric, keyed by metric → {period, low, high, unit, quote, page, link}."""
    out: dict[str, dict] = {}
    for g in conn.execute(
            "SELECT g.*, d.kind, d.path FROM guidance g LEFT JOIN documents d ON d.id=g.doc_id "
            "WHERE g.sid=? AND g.verified >= 0.5 AND (g.low IS NOT NULL OR g.high IS NOT NULL) "
            "ORDER BY g.stated_on", (sid,)):
        key = g["metric"] if (g["scope"] or "total") == "total" else f"{g['metric']}:{g['scope']}"
        out[key] = dict(g)
    return out


def _next12(value_fy: float, fy: int, base: float, base_end: str | None) -> float | None:
    """A fiscal-year figure → the next-twelve-months equivalent: annualise the growth from the trailing
    period (its middle) to that fiscal year (its middle), then apply one year of it to the trailing base."""
    if not base or not value_fy or not base_end:
        return None
    base_mid = metrics.months_before(date.fromisoformat(base_end), 6)
    fy_mid = date(fy - 1, 9, 30)
    gap = (fy_mid.year - base_mid.year) * 12 + fy_mid.month - base_mid.month
    if gap < 3:
        return None
    g = (value_fy / base) ** (12 / gap) - 1
    return round(base * (1 + _clip(g, -0.5, 1.0)), 1)


def _fy_of(period: str | None) -> int | None:
    m = re.search(r"FY\s*'?(\d{2,4})", period or "", re.I)
    if not m:
        return None
    y = int(m.group(1))
    return y + 2000 if y < 100 else y


# ── forward estimates ───────────────────────────────────────────────────────

def forward(conn: sqlite3.Connection, s, settings: dict, pts: list[dict], q: dict) -> dict:
    cfg = settings["score"]
    today = now_ist().date()
    fy = _fy(today)
    rev = metrics.revenue(conn, s)
    n_q = q.get("n", 4)
    if len(q.get("sales") or []) >= n_q and all(v is not None for v in q["sales"][-n_q:]):
        # the same series as EPS and the trend (metrics.revenue can be another source, period or — for a
        # half-yearly company — a two-year sum)
        from . import model
        lab = q["labels"][-1]
        mm = re.match(r"([QH])(\d) FY(\d{2})", lab)
        end = model.period_end(2000 + int(mm.group(3)), int(mm.group(2)), mm.group(1) == "H").isoformat() if mm else rev.get("period_end")
        rev = {"value": round(sum(q["sales"][-n_q:]), 2), "label": f"TTM to {lab} · {q.get('source')}", "period_end": end}
    rev_ttm = rev["value"]
    sc = metrics.screener_summary(s["folder"])
    annual = (sc.get("annual") or {}).get("Sales") or (sc.get("annual") or {}).get("Revenue") or {}
    last_fy_rev = annual.get(f"Mar {fy - 1}")
    guid = _guidance(conn, s["sid"])
    options = []

    end = rev.get("period_end")
    g = guid.get("revenue")
    gfy = _fy_of(g["period"]) if g else None
    if g and re.match(r"\s*[QH][1-4]", g.get("period") or "", re.I):
        g = None                       # a quarter's or a half's target is not the year's revenue
    if g and gfy in (fy, fy + 1) and g["unit"] in ("cr", None):
        mid = ((g["low"] or g["high"]) + (g["high"] or g["low"])) / 2
        n12 = _next12(mid, gfy, rev_ttm, end)
        if n12:
            options.append({"basis": "guidance", "value": n12, "fy": gfy, "quote": g["quote"], "page": g["page"],
                            "note": f"guided ₹{mid:,.0f} Cr for FY{str(gfy)[2:]} → next 12 months"})
    g = guid.get("revenue_growth")
    if g and re.match(r"\s*[QH][1-4]", g.get("period") or "", re.I):
        g = None
    gfy = (_fy_of(g["period"]) or fy) if g else None
    if g and gfy in (fy, fy + 1) and (last_fy_rev or rev_ttm):
        mid = ((g["low"] if g["low"] is not None else g["high"]) + (g["high"] if g["high"] is not None else g["low"])) / 2
        prior = annual.get(f"Mar {gfy - 1}") or (last_fy_rev if gfy == fy + 1 and last_fy_rev else None)
        if gfy == fy + 1 and prior is last_fy_rev and last_fy_rev:
            prior = last_fy_rev * (1 + mid / 100)          # FY+1 growth is on the current year's revenue
        target = (prior or rev_ttm) * (1 + mid / 100)
        n12 = _next12(target, gfy, rev_ttm, end) if prior else round(rev_ttm * (1 + mid / 100), 1)
        if n12:
            options.append({"basis": "guidance", "value": n12, "fy": gfy, "quote": g["quote"], "page": g["page"],
                            "note": f"guided {mid:g}% growth for FY{str(gfy)[2:]} → next 12 months"})
    if pts and rev_ttm:
        book = pts[-1]["value_cr"]
        ex = conn.execute("SELECT value, quote, page FROM doc_facts WHERE sid=? AND section='execution' AND verified >= 0.5 "
                          "ORDER BY id DESC LIMIT 1", (s["sid"],)).fetchone()
        default = cfg["default_execution_months"]
        months = ex["value"] if ex and ex["value"] and 3 <= ex["value"] <= 120 else default
        how = " (stated)" if ex and ex["value"] == months else " (assumed)"
        if book / max(1.0, months / 12) > 2.5 * rev_ttm and months != default:
            months, how = default, f" (assumed; the stated {ex['value']:g} months would mean >2.5× revenue a year)"
        yearly = book / max(1.0, months / 12)
        if book / rev_ttm >= 0.5:      # order-driven business: next 12 months ≈ what the book will execute
            options.append({"basis": "order_book", "value": round(max(yearly, 0.0), 1), "fy": None,
                            "note": f"₹{book:,.0f} Cr book over {months:g} months{how}"})
    sales = q.get("sales") or []
    n = q.get("n", 4)
    t0, t1 = _ttm(sales[:-n], n) if len(sales) >= 2 * n else None, _ttm(sales, n)
    trend = round((t1 / t0 - 1) * 100, 1) if t0 and t1 and t0 > 0 else None
    if rev_ttm and trend is not None:
        options.append({"basis": "trend", "value": round(rev_ttm * (1 + _clip(trend, -30, 60) / 100), 1), "fy": None,
                        "note": f"trailing revenue growth {trend:+.0f}% carried forward"})
    chosen = next((o for b in ("guidance", "order_book", "trend") for o in options if o["basis"] == b), None)

    eps = q.get("eps") or []
    eps_ttm, eps_prev = _ttm(eps, n), (_ttm(eps[:-n], n) if len(eps) >= 2 * n else None)
    eps_g = round((eps_ttm / eps_prev - 1) * 100, 1) if eps_ttm and eps_prev and eps_prev > 0 else None
    opm = [v for v in (q.get("opm") or []) if v is not None]
    opm_ttm = sum(opm[-n:]) / n if len(opm) >= n else None
    opm_prev = sum(opm[-2 * n:-n]) / n if len(opm) >= 2 * n else None
    margin_g = guid.get("ebitda_margin")
    margin_factor, margin_note = 1.0, "at the current margin"
    if margin_g and opm_ttm and opm_ttm > 0 and (margin_g["low"] or margin_g["high"]):
        gm = ((margin_g["low"] or margin_g["high"]) + (margin_g["high"] or margin_g["low"])) / 2
        margin_factor = _clip(gm / opm_ttm, 0.7, 1.3)          # a guided margin moves EPS, within reason
        margin_note = f"at the guided {gm:g}% EBITDA margin (now {opm_ttm:.1f}%)"
    fwd_rev = chosen["value"] if chosen else None
    fwd_eps = None
    if fwd_rev and rev_ttm and eps_ttm and eps_ttm > 0:
        fwd_eps = round(eps_ttm * fwd_rev / rev_ttm * margin_factor, 2)
    close = s["close"]
    return {
        "revenue_ttm": rev_ttm, "revenue_label": rev["label"], "fy": fy,
        "revenue_fwd": fwd_rev, "basis": chosen["basis"] if chosen else None, "options": options,
        "revenue_growth_pct": round((fwd_rev / rev_ttm - 1) * 100, 1) if fwd_rev and rev_ttm else None,
        "revenue_add_cr": round(fwd_rev - rev_ttm, 1) if fwd_rev and rev_ttm else None,
        "eps_ttm": round(eps_ttm, 2) if eps_ttm is not None else None, "eps_growth_pct": eps_g,
        "eps_fwd": fwd_eps, "eps_note": margin_note,
        "pe_ttm": round(close / eps_ttm, 1) if close and eps_ttm and eps_ttm > 0 else None,
        "pe_fwd": round(close / fwd_eps, 1) if close and fwd_eps and fwd_eps > 0 else None,
        "opm_ttm": round(opm_ttm, 1) if opm_ttm is not None else None,
        "opm_prev": round(opm_prev, 1) if opm_prev is not None else None,
        "trend_growth_pct": trend, "fin_source": q.get("source"), "guidance": guid,
    }


# ── score ───────────────────────────────────────────────────────────────────

def _hit_rate(folder: str | None) -> tuple[float | None, int]:
    ta = _report(folder, "transcript_analysis") or {}
    v = [str(g.get("verdict", "")).lower() for g in ta.get("guidance_vs_delivery") or [] if isinstance(g, dict)]
    good = sum(x in ("delivered", "exceeded", "largely_delivered") for x in v)
    part = sum(x in ("partially_delivered", "partly") for x in v)
    bad = sum(x == "missed" for x in v)
    n = good + part + bad
    return ((good + 0.5 * part) / n if n else None), n


def components(conn: sqlite3.Connection, s, settings: dict, pts: list[dict], fwd: dict) -> list[dict]:
    out: list[dict] = []

    def add(key: str, points: float | None, reason: str, data: dict | None = None):
        out.append({"key": key, "points": None if points is None else round(_clip(points), 2), "reason": reason,
                    "data": data or {}})

    # order book
    g = metrics.growth(pts, 12) or metrics.growth(pts, 6)
    if g:
        p = 2 if g["pct"] >= 30 else 1 if g["pct"] >= 10 else 0 if g["pct"] > -10 else -1 if g["pct"] > -25 else -2
        add("order_book_growth", p, f"Order book {g['pct']:+.0f}% (₹{g['from']:,.0f} → ₹{g['to']:,.0f} Cr since "
                                     f"{date.fromisoformat(g['from_as_of']).strftime('%b %Y')})", g)
    else:
        add("order_book_growth", None, "No order-book history long enough" if pts else "No order book on record")
    if pts and fwd["revenue_ttm"]:
        br = pts[-1]["value_cr"] / fwd["revenue_ttm"]
        p = 2 if br >= 3 else 1 if br >= 1.5 else 0 if br >= 0.5 else -1
        add("book_to_revenue", p, f"Order book {br:.1f}× trailing revenue", {"x": round(br, 2)})
    else:
        add("book_to_revenue", None, "No order book or revenue")

    # management credibility
    hr, n = _hit_rate(s["folder"])
    if hr is not None and n >= 2:
        p = 1.5 if hr >= 0.75 else 0.5 if hr >= 0.5 else -0.5 if hr >= 0.35 else -1.5
        add("guidance_delivery", p, f"Delivered {hr * 100:.0f}% of past guidance ({n} checks, concall analysis)", {"n": n})
    else:
        add("guidance_delivery", None, "Too few guidance-vs-delivery checks")

    # margins & earnings
    if fwd["opm_ttm"] is not None and fwd["opm_prev"] is not None:
        d = fwd["opm_ttm"] - fwd["opm_prev"]
        p = 1 if d >= 2 else 0.5 if d >= 0.75 else -1 if d <= -2 else -0.5 if d <= -0.75 else 0
        add("margin_trend", p, f"Operating margin {fwd['opm_prev']:.1f}% → {fwd['opm_ttm']:.1f}% (last 4Q vs prior 4Q, {fwd['fin_source']})")
    else:
        add("margin_trend", None, "Not enough quarterly margins")
    eg, pe = fwd["eps_growth_pct"], fwd["pe_ttm"]
    if eg is not None and pe:
        if eg <= -10:
            p, why = -2, f"EPS down {abs(eg):.0f}% (TTM) at P/E {pe:.0f}"
        else:
            g_used = min(eg, 60.0)          # a rebound from a low base is not a growth rate
            peg = pe / g_used if eg > 0 else 99
            p = 2 if peg < 1 else 1 if peg < 1.5 else 0 if peg < 2.5 else -1
            why = (f"EPS {eg:+.0f}% (TTM) at P/E {pe:.0f} → PEG {peg:.1f}"
                   + (" (growth capped at 60% — base effect)" if eg > 60 else "")) if eg > 0 else f"EPS flat at P/E {pe:.0f}"
        add("eps_vs_pe", p, why, {"peg": round(pe / min(eg, 60.0), 2) if eg and eg > 0 else None})
    else:
        add("eps_vs_pe", None, "No EPS growth / P/E (loss-making or too little history)")

    # technicals
    ph = s["phase"]
    if ph in PHASE_POINTS:
        p = PHASE_POINTS[ph]
        extra = ""
        if s["dma200"] and s["close"]:
            if s["close"] < s["dma200"]:
                p -= 0.5
                extra = " · below DMA200"
            else:
                extra = " · above DMA200"
        add("technical", p, f"Trend phase {ph}{extra}")
    else:
        add("technical", None, "No price history")

    # holdings
    sh = conn.execute("SELECT * FROM shareholding WHERE sid=? AND promoters IS NOT NULL ORDER BY qdate DESC LIMIT 2",
                      (s["sid"],)).fetchall()
    if len(sh) == 2:
        dp = sh[0]["promoters"] - sh[1]["promoters"]
        p = 1 if dp >= 0.5 else -2 if dp <= -2 else -1 if dp <= -0.75 else 0
        add("promoter_flow", p, f"Promoters {sh[1]['promoters']:.2f}% → {sh[0]['promoters']:.2f}% ({dp:+.2f} pp)")
        if sh[0]["fiis"] is not None and sh[1]["fiis"] is not None and sh[0]["diis"] is not None and sh[1]["diis"] is not None:
            di = (sh[0]["fiis"] + sh[0]["diis"]) - (sh[1]["fiis"] + sh[1]["diis"])
            p = 1 if di >= 1 else 0.5 if di >= 0.3 else -1 if di <= -1.5 else -0.5 if di <= -0.5 else 0
            add("institution_flow", p, f"FII + DII {di:+.2f} pp last quarter")
        else:
            add("institution_flow", None, "No FII / DII split")
    else:
        add("promoter_flow", None, "Fewer than two quarters of shareholding")
        add("institution_flow", None, "Fewer than two quarters of shareholding")
    since = (now_ist().date() - timedelta(days=150)).isoformat()
    moves = conn.execute("SELECT type, COUNT(*) n FROM events WHERE sid=? AND d >= ? AND type IN "
                         "('anchor_entry','anchor_exit','anchor_up','anchor_down','investor_entry','investor_exit',"
                         "'investor_up','investor_down') GROUP BY type", (s["sid"], since)).fetchall()
    mv = {r["type"]: r["n"] for r in moves}
    pos = mv.get("anchor_entry", 0) + mv.get("anchor_up", 0) + 2 * mv.get("investor_entry", 0) + mv.get("investor_up", 0)
    neg = mv.get("anchor_exit", 0) + mv.get("anchor_down", 0) + 2 * mv.get("investor_exit", 0) + mv.get("investor_down", 0)
    if pos or neg:
        add("anchor_flow", 0.5 * (pos - neg), f"Named holders: {pos} buying signals, {neg} selling (anchor / tracked investors, 5 months)")
    else:
        add("anchor_flow", None, "No named-holder moves recently")
    pl = conn.execute("SELECT COUNT(*) FROM events WHERE sid=? AND d >= ? AND type IN ('pledge_reg31','pledge','pledge_reason')",
                      (s["sid"], (now_ist().date() - timedelta(days=90)).isoformat())).fetchone()[0]
    add("pledge", -1.5 if pl else 0, f"{pl} pledge / encumbrance filing{'s' if pl != 1 else ''} in 90 days" if pl
        else "No pledge filings in 90 days")

    # valuation (existing research)
    val = _report(s["folder"], "valuation_agent") or {}
    lo, hi = to_float(val.get("fair_value_low")), to_float(val.get("fair_value_high"))
    if lo and hi and s["close"]:
        c = s["close"]
        p = 1.5 if c < lo * 0.9 else 1 if c < lo else 0 if c <= hi else -1 if c <= hi * 1.25 else -2
        add("valuation", p, f"Price ₹{c:,.0f} vs fair value ₹{lo:,.0f}–{hi:,.0f} (research, {str(val.get('generated_at') or '')[:10]})")
    else:
        add("valuation", None, "No fair-value range in research")
    return out


def action(total: float, have: int, held: bool, cfg: dict) -> str:
    if have < cfg["min_components"]:
        return "Hold" if held else "Watch"
    if held:
        return "Add" if total >= cfg["add_at"] else "Hold" if total > cfg["trim_at"] else "Trim" if total > cfg["exit_at"] else "Exit"
    return "Add" if total >= cfg["watch_add_at"] else "Watch"


def score_stock(conn: sqlite3.Connection, s, settings: dict, pts: list[dict]) -> dict:
    cfg = settings["score"]
    q = quarterly(conn, s)
    fwd = forward(conn, s, settings, pts, q)
    comps = components(conn, s, settings, pts, fwd)
    w = cfg["weights"]
    for c in comps:
        c["weight"] = w.get(c["key"], 1.0)
        c["weighted"] = round(c["points"] * c["weight"], 2) if c["points"] is not None else None
    total = round(sum(c["weighted"] for c in comps if c["weighted"] is not None), 2)
    have = sum(1 for c in comps if c["points"] is not None)
    held = bool(s["in_portfolio"])
    computed = action(total, have, held, cfg)
    note = conn.execute("SELECT m.stance, n.d, n.title FROM note_mentions m JOIN notes n ON n.id=m.note_id "
                        "WHERE m.sid=? AND m.stance IN ('add','reduce','exit','watch','hold','avoid') ORDER BY n.d DESC LIMIT 1",
                        (s["sid"],)).fetchone()
    final = NOTE_ACTION.get(note["stance"]) if note else computed
    reasons = sorted([c for c in comps if c["weighted"]], key=lambda c: -abs(c["weighted"]))
    return {
        "sid": s["sid"], "name": s["name"], "nse": s["nse_symbol"], "bse": s["bse_code"], "sector": s["sector"],
        "held": held, "in_watchlist": bool(s["in_watchlist"]), "close": s["close"], "phase": s["phase"],
        "score": total, "components_with_data": have, "components_total": len(comps),
        "computed_action": computed, "action": final,
        "note": {"stance": note["stance"], "d": note["d"], "title": note["title"]} if note else None,
        "top_reasons": [{"text": c["reason"], "w": c["weighted"]} for c in reasons[:3]],
        "components": comps, "forward": {k: v for k, v in fwd.items() if k != "guidance"},
        "guidance": list(fwd["guidance"].values()),
        "order_book_cr": pts[-1]["value_cr"] if pts else None,
    }


def compute_all(conn: sqlite3.Connection, settings: dict, scope_sql: str) -> list[dict]:
    stocks = conn.execute(f"SELECT s.*, t.close, t.phase, t.dma200, t.dma40 FROM stocks s LEFT JOIN technicals t USING(sid) "
                          f"WHERE {scope_sql}").fetchall()
    series = metrics.ob_points(conn, [s["sid"] for s in stocks])
    return [score_stock(conn, s, settings, series.get(s["sid"], [])) for s in stocks]
