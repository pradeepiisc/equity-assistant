"""
End-of-day movers — the tracked stocks (holdings and watchlist) whose price moved unusually today, and why — as one
"Movers" card with a table, after the day's prices and signals are in.

Significant is judged against the stock itself, not one threshold for all:
  • main board: |move| ≥ 4% and ≥ 2× its own daily volatility (σ of the last 60 sessions), or ≥ 3σ whatever the
    size, or ≥ 2.5% on ≥ 3× its usual volume (median of the last 20 sessions)
  • SME (often at a circuit): a move larger than any of its last 5 sessions on ≥ 2× its 5-session average volume,
    or ≥ 3σ
  • trend changes today — DMA200 crossed, golden / death cross, entered Strong Bullish / Bearish — are listed too
Ranked by surprise (|σ| plus volume), holdings first among equals.

Why: the tracker's own events for the company in the last 36 hours first (results, orders, fund raising, ratings,
deals, pledges, policy, the previous session's crosses), then news headlines from Google News' search feed (public, no
key). The local model (localllm.py) names the specific trigger — or says none was found — and whether it calls for a
look (review: the story may have changed; watch: technical / sentiment; none: noise). No paid LLM. Reasons are filled
in a few stocks per tick, so the card appears at once and completes over the next minutes.
"""
from __future__ import annotations

import html
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from .db import add_event
from .util import Http, now_ist

DEFAULTS = {"min_move_pct": 4.0, "z_main": 2.0, "z_any": 3.0, "vol_move_pct": 2.5, "vol_x": 3.0, "sme_vol_x": 2.0,
            "sme_min_pct": 3.0, "max_rows": 20, "per_tick": 5}
TREND_TYPES = {"cross_dma200_up": "crossed above DMA200", "cross_dma200_down": "fell below DMA200",
               "golden_cross": "golden cross", "death_cross": "death cross",
               "phase_bullish": "entered Strong Bullish", "phase_bearish": "entered Strong Bearish"}
NEWS_URL = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"

PROMPT = """For each Indian listed company below, its share price moved unusually on {day}. Using ONLY the evidence
given — the tracker's own records of the company's filings and signals, and today's news headlines — say why: the
specific, company-related trigger (name the filing, order, result or headline), not market-wide or generic reasons.
If the evidence has no specific trigger, say "no specific trigger found" (a market-wide move or a technical breakout
is acceptable only when the evidence shows it). Then say whether it calls for the investor's attention:
"review" (the business story may have changed — results, guidance, orders, fund raising, governance, rating),
"watch" (a technical or sentiment move to keep an eye on), or "none" (noise).

{blocks}

Return ONE JSON object: {{"rows": [{{"sid": "as given", "trigger": "results | order | fund raise | rating | deal |
policy | news | technical | none", "reason": "one or two lines, specific", "source": "the event or headline used",
"action": "review | watch | none", "action_note": "one short line"}}]}}
JSON only."""


def _cfg(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("movers") or {})}


def scan(conn: sqlite3.Connection, settings: dict, day: str) -> list[dict]:
    """The day's significant moves among held and watched stocks, most surprising first."""
    from . import prices

    cfg = _cfg(settings)
    trend = {}
    for e in conn.execute("SELECT sid, type FROM events WHERE kind='technical' AND d=? AND type IN (%s)"
                          % ",".join("?" * len(TREND_TYPES)), (day, *TREND_TYPES)):
        trend.setdefault(e["sid"], []).append(TREND_TYPES[e["type"]])
    out = []
    for s in conn.execute("SELECT * FROM stocks WHERE in_portfolio=1 OR in_watchlist=1").fetchall():
        df = prices.adjusted_series(conn, s)
        if len(df) < 25 or df.index[-1].date().isoformat() != day:
            continue
        r = df["close"].pct_change() * 100
        ret = float(r.iloc[-1])
        hist = r.iloc[-61:-1].dropna()
        sigma = float(hist.std()) if len(hist) >= 15 else None
        z = ret / sigma if sigma else None
        vol = float(df["volume"].iloc[-1] or 0)
        why = []
        if s["is_sme"]:
            base = float(df["volume"].iloc[-6:-1].mean() or 0)
            vx = vol / base if base else None
            prior = float(r.iloc[-6:-1].abs().max())
            if abs(ret) >= cfg["sme_min_pct"] and abs(ret) > prior and vx and vx >= cfg["sme_vol_x"]:
                why.append(f"beyond its 5-day range on {vx:.1f}× volume")
        else:
            base = float(df["volume"].iloc[-21:-1].median() or 0)
            vx = vol / base if base else None
            if abs(ret) >= cfg["min_move_pct"] and z is not None and abs(z) >= cfg["z_main"]:
                why.append(f"{abs(z):.1f}σ move")
            elif abs(ret) >= cfg["vol_move_pct"] and vx and vx >= cfg["vol_x"]:
                why.append(f"on {vx:.1f}× volume")
        if z is not None and abs(z) >= cfg["z_any"] and not any("σ" in w for w in why):
            why.append(f"{abs(z):.1f}σ move")
        if s["sid"] in trend:
            why += trend[s["sid"]]
        if not why:
            continue
        score = (abs(z) if z else abs(ret) / 2) + (math.log2(vx) if vx and vx > 1 else 0) + \
            (1.0 if s["in_portfolio"] else 0) + (1.0 if s["sid"] in trend else 0)
        out.append({"sid": s["sid"], "name": s["name"], "held": bool(s["in_portfolio"]), "sme": bool(s["is_sme"]),
                    "ret": round(ret, 2), "z": round(z, 1) if z is not None else None,
                    "vol_x": round(vx, 1) if vx else None, "close": round(float(df["close"].iloc[-1]), 2),
                    "why": why, "trend": trend.get(s["sid"], []), "score": round(score, 2)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[: cfg["max_rows"]]


def evidence(conn: sqlite3.Connection, sid: str, day: str) -> list[str]:
    """The tracker's own records for the company since the previous session's close."""
    since = (datetime.fromisoformat(day) - timedelta(days=1)).isoformat() + "T15:30"
    out = []
    for e in conn.execute("SELECT ts, kind, type, severity, title, detail FROM events WHERE sid=? AND ts >= ? "
                          "AND type NOT IN ('daily_movers', 'daily_technicals') ORDER BY ts", (sid, since)):
        detail = re.sub(r"\s+", " ", e["detail"] or "")[:240]
        out.append(f"[{e['ts'][:16]} · {e['kind']}] {e['title']}" + (f" — {detail}" if detail else ""))
    for e in conn.execute("SELECT p.title, p.ministry FROM events e JOIN policy_releases p ON p.prid=json_extract(e.data, '$.prid') "
                          "WHERE e.type='policy_release' AND e.ts >= ? AND e.data LIKE ?", (since, f'%"sid": "{sid}"%')):
        out.append(f"[policy] {e['title']} ({e['ministry']})")
    return out[-12:]


def news(http: Http, name: str, day: str) -> list[str]:
    """Headlines about the company from the last ~36 hours (Google News search feed)."""
    # the full name less "Limited": "Deep" alone finds Nebius, "Xpro" the US company Expro
    short = re.sub(r"\s*\b(limited|ltd)\b\.?\s*$", "", name.strip(), flags=re.I).strip() or name
    q = quote_plus(f'"{short}" share price')
    try:
        r = http.get(NEWS_URL.format(q=q))
    except Exception:
        return []
    if r.status_code != 200:
        return []
    cutoff = datetime.fromisoformat(day + "T23:59:59+05:30") - timedelta(hours=40)
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:20]:
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        src = re.search(r"<source[^>]*>(.*?)</source>", item, re.S)
        try:
            when = parsedate_to_datetime(pub.group(1)) if pub else None
        except (TypeError, ValueError):
            when = None
        if not title or (when and when < cutoff):
            continue
        t = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1))).strip()
        out.append(f"{t} ({html.unescape(src.group(1)) if src else 'news'}, {when.strftime('%d %b %H:%M') if when else ''})")
    return out[:8]


def _block(m: dict) -> str:
    move = f"{'+' if m['ret'] >= 0 else ''}{m['ret']:.1f}%"
    lines = [f"### {m['sid']} — {m['name']}: {move} to ₹{m['close']:,.2f}"
             + (f", {m['z']:+.1f}σ" if m["z"] is not None else "") + (f", volume {m['vol_x']}× usual" if m["vol_x"] else "")
             + (f"; {', '.join(m['trend'])}" if m["trend"] else ""),
             f"What it does: {m.get('profile') or 'not recorded'}",
             "Tracker records (last 36 h): " + ("; ".join(m["evidence"]) if m["evidence"] else "none"),
             "News headlines: " + ("; ".join(m["news"]) if m["news"] else "none found")]
    return "\n".join(lines)


def _card(conn: sqlite3.Connection, day: str, movers: list[dict], pending: int) -> None:
    rows = []
    for m in movers:
        rows.append({"sid": m["sid"], "name": m["name"], "held": m["held"],
                     "move": f"{'+' if m['ret'] >= 0 else ''}{m['ret']:.1f}%", "tone": "pos" if m["ret"] >= 0 else "neg",
                     "volx": f"{m['vol_x']}×" if m["vol_x"] else None, "sig": ", ".join(m["why"]),
                     "reason": m.get("reason") or ("finding the reason…" if not m.get("done") else "no specific trigger found"),
                     "action": m.get("action")})
    n_held = sum(1 for m in movers if m["held"])
    review = [m for m in movers if m.get("action") == "review" and m["held"]]
    sev = "high" if review else "medium"
    title = f"Movers · {datetime.fromisoformat(day).strftime('%d %b')}: {len(movers)} stocks moved unusually" + \
        (f", {n_held} held" if n_held else "") + (f" — {len(review)} to review" if review else "")
    detail = "Up: " + ", ".join(f"{m['sid']} {m['ret']:+.1f}%" for m in movers if m["ret"] >= 0)[:300] + \
        "\nDown: " + ", ".join(f"{m['sid']} {m['ret']:+.1f}%" for m in movers if m["ret"] < 0)[:300]
    table = {"columns": [{"key": "sid", "label": "Company"}, {"key": "move", "label": "Move", "num": True, "tone": True},
                         {"key": "volx", "label": "Volume", "num": True}, {"key": "sig", "label": "Why flagged", "wide": True},
                         {"key": "reason", "label": "Trigger", "wide": True}, {"key": "action", "label": "Action"}],
             "rows": rows, "note": ("Reasons for the rest arrive over the next few ticks. " if pending else "") +
             "Significant = its own volatility (σ of 60 sessions) and volume; SMEs against their last 5 sessions."}
    data = {"day": day, "sids": [m["sid"] for m in movers], "table": table, "movers": movers}
    dedupe = f"movers:{day}"
    if conn.execute("SELECT 1 FROM events WHERE dedupe=?", (dedupe,)).fetchone():
        conn.execute("UPDATE events SET title=?, detail=?, severity=?, data=? WHERE dedupe=?",
                     (title, detail, sev, json.dumps(data, default=str, ensure_ascii=False), dedupe))
    else:
        add_event(conn, sid=None, ts=f"{day}T18:30:00+05:30", kind="movers", type="daily_movers", severity=sev,
                  title=title, detail=detail, source="NSE / BSE bhavcopy · tracker events · Google News", dedupe=dedupe,
                  data=data)


def run(conn: sqlite3.Connection, http: Http, settings: dict, explain: bool = True) -> dict:
    """Scan once per session (after its prices and signals); then fill reasons a few stocks per tick (`explain`)."""
    from . import localllm, technicals

    cfg = _cfg(settings)
    latest = technicals.latest_trading_day(conn)
    if latest is None:
        return {"skipped": "no prices"}
    day = latest.isoformat()
    row = conn.execute("SELECT data FROM events WHERE dedupe=?", (f"movers:{day}",)).fetchone()
    if row is None:
        movers = scan(conn, settings, day)
        if not movers:
            return {"day": day, "movers": 0}
        prof = {r["sid"]: r for r in conn.execute("SELECT p.sid, p.sector, p.description, json_extract(v.view, '$.what_it_does') what "
                                                  "FROM stocks s LEFT JOIN company_profiles p ON p.sid=s.sid "
                                                  "LEFT JOIN company_view v ON v.sid=s.sid")}
        for m in movers:
            p = prof.get(m["sid"])
            m["profile"] = ((p["sector"] or "") + ". " + (p["description"] or p["what"] or ""))[:300] if p else ""
            m["evidence"] = evidence(conn, m["sid"], day)
            m["news"] = news(http, m["name"], day)
        _card(conn, day, movers, pending=len(movers))
        conn.commit()
    else:
        movers = json.loads(row["data"] or "{}").get("movers") or []
    todo = [m for m in movers if not m.get("done")][: cfg["per_tick"]]
    if not todo:
        return {"day": day, "movers": len(movers), "pending": 0}
    if not explain:
        return {"day": day, "movers": len(movers), "pending": len(todo)}
    if not localllm.available(settings):
        return {"day": day, "movers": len(movers), "pending": len(todo), "waiting": "local model not running"}
    prompt = PROMPT.format(day=day, blocks="\n\n".join(_block(m) for m in todo))
    data = localllm.chat_json(conn, settings, prompt, purpose="movers", max_tokens=1800,
                              system="You are a careful equity analyst. Output one strict JSON object only — no prose, no code fences.")
    got = {r.get("sid"): r for r in data.get("rows") or [] if isinstance(r, dict)}
    for m in todo:
        r = got.get(m["sid"]) or {}
        m["reason"] = (r.get("reason") or "no specific trigger found").strip()
        m["trigger"] = r.get("trigger")
        m["action"] = r.get("action") or "none"
        m["action_note"] = r.get("action_note")
        m["done"] = True
    left = sum(1 for m in movers if not m.get("done"))
    _card(conn, day, movers, pending=left)
    conn.commit()
    return {"day": day, "movers": len(movers), "explained": len(todo), "pending": left}


def pending_reasons(conn: sqlite3.Connection) -> int:
    """Movers of the latest card still waiting for their reason."""
    row = conn.execute("SELECT data FROM events WHERE type='daily_movers' ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return 0
    return sum(1 for m in (json.loads(row["data"] or "{}").get("movers") or []) if not m.get("done"))
