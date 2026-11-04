"""
Technical milestones from adjusted daily closes.

Per stock: DMA 20/40/50/100/200, 52-week range, volume vs 20-day average and the
trend phase (same rule as workflows/shared_sections.classify_trend, price vs
DMA40 vs DMA100). Events:

  cross_dma{N}_up / _down   close crosses DMA N (confirmed by `cross_confirm_pct`)
  golden_cross / death_cross   DMA50 crosses DMA200
  phase_bullish / phase_bearish   entered Strong Bullish / Bearish
  high_52w / low_52w           first new 52-week high/low in 20 sessions
  volume_spike                 volume ≥ N× 20-day average with a real price move
  corporate_action             split/bonus adjustment detected in the price series
"""
from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from . import prices
from .db import add_event
from .util import now_ist

PHASES = ["Strong Bullish", "Bullish Pullback", "Neutral", "Bearish Rally", "Bearish"]
WINDOWS = (20, 40, 50, 100, 200)


def classify(price: float, fast: float | None, slow: float | None, band_pct: float = 3.0) -> str:
    if fast is None or slow is None or np.isnan(fast) or np.isnan(slow):
        return "No data"
    if price > fast and fast > slow:
        return "Strong Bullish"
    if price > slow and price <= fast:
        return "Bullish Pullback"
    if abs(price - slow) / slow * 100 < band_pct:
        return "Neutral"
    if price < slow and price >= fast:
        return "Bearish Rally"
    if price < slow:
        return "Bearish"
    return "Neutral"


def indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    for w in sorted(set(WINDOWS) | set(cfg["cross_lines"]) | set(cfg["golden_cross"])
                    | {cfg["phase_fast"], cfg["phase_slow"]}):
        out[f"dma{w}"] = out["close"].rolling(w, min_periods=w).mean()
    out["hi52"] = out["high"].rolling(250, min_periods=1).max()
    out["lo52"] = out["low"].rolling(250, min_periods=1).min()
    out["prior_hi52"] = out["high"].shift(1).rolling(250, min_periods=120).max()
    out["prior_lo52"] = out["low"].shift(1).rolling(250, min_periods=120).min()
    out["vol_avg20"] = out["volume"].shift(1).rolling(20, min_periods=10).mean()
    out["chg_pct"] = out["close"].pct_change() * 100
    fast, slow, band = f"dma{cfg['phase_fast']}", f"dma{cfg['phase_slow']}", cfg["neutral_band_pct"]
    out["phase"] = [classify(p, f, s, band) for p, f, s in zip(out["close"], out[fast], out[slow])]
    return out


def _side_series(values: pd.Series, line: pd.Series, buf: float) -> list[int | None]:
    """+1 above / -1 below; inside the ±buf band the previous side is kept (hysteresis)."""
    state: int | None = None
    out: list[int | None] = []
    for v, l in zip(values, line):
        if pd.isna(l) or pd.isna(v):
            out.append(state)
            continue
        if v > l * (1 + buf):
            state = 1
        elif v < l * (1 - buf):
            state = -1
        elif state is None:
            state = 1 if v >= l else -1
        out.append(state)
    return out


def detect_events(ind: pd.DataFrame, cfg: dict) -> list[dict]:
    """All milestone events in the series (oldest first)."""
    events: list[dict] = []
    idx = list(ind.index)
    buf = cfg["cross_confirm_pct"] / 100

    def flips(sides: list[int | None]):
        prev = None
        for i, s in enumerate(sides):
            if prev is not None and s is not None and s != prev:
                yield i, s
            if s is not None:
                prev = s

    for w in cfg["cross_lines"]:
        col = f"dma{w}"
        for i, s in flips(_side_series(ind["close"], ind[col], buf)):
            r = ind.iloc[i]
            up = s > 0
            events.append({
                "d": idx[i], "type": f"cross_dma{w}_{'up' if up else 'down'}",
                "title": f"Closed {'above' if up else 'below'} DMA{w}",
                "detail": f"Close ₹{r['close']:,.2f} vs DMA{w} ₹{r[col]:,.2f}",
                "data": {"close": r["close"], "line": r[col], "window": w},
            })

    f, s_ = cfg["golden_cross"]
    for i, s in flips(_side_series(ind[f"dma{f}"], ind[f"dma{s_}"], 0.001)):
        r = ind.iloc[i]
        up = s > 0
        events.append({
            "d": idx[i], "type": "golden_cross" if up else "death_cross",
            "title": f"{'Golden' if up else 'Death'} cross — DMA{f} {'above' if up else 'below'} DMA{s_}",
            "detail": f"DMA{f} ₹{r[f'dma{f}']:,.2f} vs DMA{s_} ₹{r[f'dma{s_}']:,.2f}",
            "data": {"fast": r[f"dma{f}"], "slow": r[f"dma{s_}"]},
        })

    fast_col, slow_col = f"dma{cfg['phase_fast']}", f"dma{cfg['phase_slow']}"
    phases = ind["phase"].tolist()
    for i in range(1, len(phases)):
        p0, p1 = phases[i - 1], phases[i]
        if p1 == p0 or "No data" in (p0, p1):
            continue
        if p1 in ("Strong Bullish", "Bearish"):
            r = ind.iloc[i]
            events.append({
                "d": idx[i], "type": "phase_bullish" if p1 == "Strong Bullish" else "phase_bearish",
                "title": f"Entered {p1} phase (from {p0})",
                "detail": (f"Close ₹{r['close']:,.2f} · DMA{cfg['phase_fast']} ₹{r[fast_col]:,.2f}"
                           f" · DMA{cfg['phase_slow']} ₹{r[slow_col]:,.2f}"),
                "data": {"from": p0, "to": p1},
            })

    last_hi = last_lo = -999
    for i in range(len(ind)):
        r = ind.iloc[i]
        if not pd.isna(r["prior_hi52"]) and r["high"] > r["prior_hi52"]:
            if i - last_hi > 20:
                events.append({"d": idx[i], "type": "high_52w", "title": "New 52-week high",
                               "detail": f"High ₹{r['high']:,.2f} (previous ₹{r['prior_hi52']:,.2f})",
                               "data": {"high": r["high"]}})
            last_hi = i
        if not pd.isna(r["prior_lo52"]) and r["low"] < r["prior_lo52"]:
            if i - last_lo > 20:
                events.append({"d": idx[i], "type": "low_52w", "title": "New 52-week low",
                               "detail": f"Low ₹{r['low']:,.2f} (previous ₹{r['prior_lo52']:,.2f})",
                               "data": {"low": r["low"]}})
            last_lo = i

    vx, vmove = cfg["volume_spike_x"], cfg["volume_spike_min_move_pct"]
    for i in range(len(ind)):
        r = ind.iloc[i]
        if pd.isna(r["vol_avg20"]) or not r["vol_avg20"] or pd.isna(r["chg_pct"]):
            continue
        x = r["volume"] / r["vol_avg20"]
        if x >= vx and abs(r["chg_pct"]) >= vmove:
            events.append({"d": idx[i], "type": "volume_spike",
                           "title": f"Volume {x:.1f}× 20-day average, price {r['chg_pct']:+.1f}%",
                           "detail": f"Volume {r['volume']:,.0f} vs avg {r['vol_avg20']:,.0f}",
                           "data": {"x": round(x, 2), "chg_pct": round(r["chg_pct"], 2)}})

    for d, fct, why in prices.adjustment_events(ind):
        events.append({"d": pd.Timestamp(d), "type": "corporate_action",
                       "title": f"Split/bonus adjustment ×{fct:.4g}" + (f" — {why}" if why else ""),
                       "detail": "Earlier prices scaled so DMAs and 52-week levels stay comparable",
                       "data": {"factor": fct, "purpose": why}})
    events.sort(key=lambda e: e["d"])
    return events


SEVERITY = {
    "cross_dma200_up": "high", "cross_dma200_down": "high",
    "golden_cross": "high", "death_cross": "high",
    "phase_bearish": "medium", "phase_bullish": "medium",
    "cross_dma40_up": "high", "cross_dma40_down": "high",       # pushed: the short-term trend turning
    "low_52w": "medium", "high_52w": "low",
    "volume_spike": "medium",
    "corporate_action": "low",
}


def _f(v) -> float | None:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 4)


def update_stock(conn: sqlite3.Connection, stock: sqlite3.Row, cfg: dict, live_since: str | None) -> int:
    """Recompute indicators + snapshot + events for one stock. Returns # new live events."""
    df = prices.adjusted_series(conn, stock)
    if df.empty:
        return 0
    ind = indicators(df, cfg)
    last = ind.iloc[-1]
    prev = ind.iloc[-2] if len(ind) > 1 else None
    phase_since = None
    phases = ind["phase"].tolist()
    for i in range(len(phases) - 1, -1, -1):
        if phases[i] != phases[-1]:
            phase_since = ind.index[i + 1].date().isoformat()
            break
    else:
        phase_since = ind.index[0].date().isoformat()

    def ret(n):
        return _f((last["close"] / ind["close"].iloc[-1 - n] - 1) * 100) if len(ind) > n else None

    conn.execute(
        "INSERT OR REPLACE INTO technicals(sid, d, close, prev_close, chg_pct, dma20, dma40, dma50, "
        "dma100, dma200, hi52, lo52, volume, vol_avg20, phase, phase_prev, phase_since, ret_1w, ret_1m, "
        "ret_3m, bars, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            stock["sid"], ind.index[-1].date().isoformat(), _f(last["close"]),
            _f(prev["close"]) if prev is not None else None, _f(last["chg_pct"]),
            _f(last.get("dma20")), _f(last.get("dma40")), _f(last.get("dma50")), _f(last.get("dma100")),
            _f(last.get("dma200")), _f(last["hi52"]), _f(last["lo52"]), _f(last["volume"]),
            _f(last["vol_avg20"]), last["phase"], prev["phase"] if prev is not None else None,
            phase_since, ret(5), ret(21), ret(63), len(ind), now_ist().isoformat(timespec="seconds"),
        ),
    )

    new_live = 0
    for e in detect_events(ind, cfg):
        d = e["d"].date().isoformat()
        backfill = live_since is None or d < live_since
        # each signal stays on the company's timeline, at low importance; the day's signals of every company reach the
        # inbox as one "Technicals" card (daily_card) instead of one alert each
        sev0 = SEVERITY.get(e["type"], "low")
        eid = add_event(
            conn, sid=stock["sid"], ts=f"{d}T15:30:00+05:30", kind="technical", type=e["type"],
            severity="low" if sev0 != "low" and e["type"] != "corporate_action" else sev0, title=e["title"],
            detail=e["detail"], source=f"{stock['price_exch']} bhavcopy", data={**(e["data"] or {}), "sev0": sev0},
            backfill=backfill, dedupe=f"tech:{stock['sid']}:{e['type']}:{d}",
        )
        if eid and not backfill:
            new_live += 1
    return new_live


def update_all(conn: sqlite3.Connection, cfg: dict, live_since: str | None) -> dict:
    n_stocks = n_events = 0
    for stock in conn.execute("SELECT * FROM stocks").fetchall():
        n_events += update_stock(conn, stock, cfg, live_since)
        n_stocks += 1
    day = latest_trading_day(conn)
    card = daily_card(conn, day.isoformat()) if day and live_since and day.isoformat() >= live_since else None
    return {"stocks": n_stocks, "new_events": n_events, "card": card}


SEV_RANK = {"high": 3, "medium": 2, "low": 1}
MAJOR = {"cross_dma200_up", "cross_dma200_down", "golden_cross", "death_cross"}     # the long-term trend turning


def daily_card(conn: sqlite3.Connection, day: str) -> int:
    """One card for the session's technical signals across held and watched stocks: a row per company with its
    signals, biggest first (DMA200 and golden / death crosses, DMA40 crosses, phase changes, 52-week, volume)."""
    import json

    rows = {}
    for e in conn.execute(
            "SELECT e.sid, e.type, e.title, e.data, s.name, s.in_portfolio, t.close, t.chg_pct, t.dma40, t.dma200, t.phase "
            "FROM events e JOIN stocks s ON s.sid=e.sid LEFT JOIN technicals t ON t.sid=e.sid "
            "WHERE e.kind='technical' AND e.d=? AND e.backfill=0 AND e.type != 'corporate_action' "
            "AND (s.in_portfolio=1 OR s.in_watchlist=1)", (day,)):
        sev0 = (json.loads(e["data"] or "{}") or {}).get("sev0") or SEVERITY.get(e["type"], "low")
        r = rows.setdefault(e["sid"], {"sid": e["sid"], "name": e["name"], "held": bool(e["in_portfolio"]), "signals": [],
                                       "rank": 0, "close": e["close"], "chg": e["chg_pct"], "phase": e["phase"],
                                       "vs40": (e["close"] / e["dma40"] - 1) * 100 if e["close"] and e["dma40"] else None,
                                       "vs200": (e["close"] / e["dma200"] - 1) * 100 if e["close"] and e["dma200"] else None})
        r["signals"].append(e["title"])
        r["rank"] = max(r["rank"], 4 if e["type"] in MAJOR else SEV_RANK.get(sev0, 1))
    if not rows:
        return 0
    ordered = sorted(rows.values(), key=lambda r: (r["rank"], r["held"], abs(r["chg"] or 0)), reverse=True)
    fmt = lambda v: f"{v:+.1f}%" if v is not None else None  # noqa: E731
    table = {"columns": [{"key": "sid", "label": "Company"}, {"key": "signal", "label": "Signal", "wide": True},
                         {"key": "day", "label": "Day", "num": True, "tone": True},
                         {"key": "v40", "label": "vs DMA40", "num": True}, {"key": "v200", "label": "vs DMA200", "num": True},
                         {"key": "phase", "label": "Trend"}],
             "rows": [{"sid": r["sid"], "name": r["name"], "held": r["held"], "signal": "; ".join(r["signals"]),
                       "day": fmt(r["chg"]), "tone": "pos" if (r["chg"] or 0) >= 0 else "neg", "v40": fmt(r["vs40"]),
                       "v200": fmt(r["vs200"]), "phase": r["phase"]} for r in ordered]}
    held_high = sum(1 for r in ordered if r["held"] and r["rank"] == 4)
    sev = "high" if held_high else "medium" if any(r["rank"] >= 2 for r in ordered) else "low"
    up = sum(1 for r in ordered if (r["chg"] or 0) >= 0)
    title = f"Technicals · {date.fromisoformat(day).strftime('%d %b')}: {len(ordered)} stocks with signals" + \
        (f", {held_high} held with a major one" if held_high else "")
    detail = f"{up} up, {len(ordered) - up} down. Major: DMA200 and golden / death crosses, DMA40 crosses."
    data = {"day": day, "sids": [r["sid"] for r in ordered], "table": table}
    dedupe = f"techday:{day}"
    if conn.execute("SELECT 1 FROM events WHERE dedupe=?", (dedupe,)).fetchone():
        conn.execute("UPDATE events SET title=?, detail=?, severity=?, data=? WHERE dedupe=?",
                     (title, detail, sev, json.dumps(data, default=str), dedupe))
        return 0
    add_event(conn, sid=None, ts=f"{day}T15:35:00+05:30", kind="technical", type="daily_technicals", severity=sev,
              title=title, detail=detail, source="NSE / BSE bhavcopy", dedupe=dedupe, data=data)
    return 1


def chart_series(conn: sqlite3.Connection, stock: sqlite3.Row, cfg: dict, days: int = 400) -> dict:
    df = prices.adjusted_series(conn, stock)
    if df.empty:
        return {"bars": []}
    ind = indicators(df, cfg).tail(days)
    cols = ["open", "high", "low", "close", "volume", "dma40", "dma50", "dma100", "dma200"]
    bars = []
    for idx, r in ind.iterrows():
        row = {"t": idx.date().isoformat()}
        for c in cols:
            row[c] = _f(r.get(c))
        row["phase"] = r["phase"]
        bars.append(row)
    return {"bars": bars}


def latest_trading_day(conn: sqlite3.Connection) -> date | None:
    return prices.latest_day(conn, "NSE") or prices.latest_day(conn, "BSE")
