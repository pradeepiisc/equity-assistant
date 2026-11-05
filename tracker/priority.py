"""
The evening priority list: every held and watched stock with a results, press-release, presentation or concall filing
in the last `priority.days` days, graded by a small-cap momentum investor's rules (the filings' summaries, the
quarterly numbers, cash and debt lines from the results, order book, capacity, guidance, and the price against its
DMAs), then ordered from "deserves more money" to "exit".

  run(settings)   grade in batches, then the rotation note; writes data/tracker/priority/<mon>-<d>-priority.md

Cloud model (`priority.client`); its calls count against llm.daily_budget_inr like any other.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
from datetime import timedelta

from .config import ROOT, TRACKER_DIR
from .db import session
from .util import now_ist

OUT_DIR = TRACKER_DIR / "priority"
KINDS = ("results", "press_release", "presentation", "transcript")

ROLE = """You are a small-cap and SME equity investor in India who runs a momentum strategy: money goes to companies
whose business is accelerating AND whose stock is in an uptrend, and comes out of companies where either breaks.
You are deciding, for a portfolio with too many stocks, which ones deserve more allocation and which to exit so the
money can go to the better ones."""

RUBRIC = """TIERS (1 = deserves the most money)
1. Everything improving: revenue, EBITDA margin, PAT and EPS up YoY (and QoQ unless the business is seasonal), growth
   visibility from the order book, order inflow or new capacity (beds for hospitals, rooms for hotels, MW, tonnes,
   stores, plants), cash from operations positive and net cash or falling debt; AND the close is above both the
   short-term (DMA40 / DMA50) and the long-term (DMA100 / DMA200) averages.
2. Margins and profit improving with the price above both short- and long-term averages, but one of revenue growth,
   visibility or cash is only average.
3. Business as strong as 1–2, but the price is below the short-term average while still above the long-term one (a
   pullback in an uptrend): add only once it is back above DMA40/50.
4. Mixed: growth with margin compression, or profit growth with weak or falling cash from operations or rising debt,
   or the price stretched / losing momentum. Hold, don't add.
5. Weakening: margins and profit falling, guidance cut or missed, or the price below the long-term average. Trim.
6. Broken: business and price both deteriorating, price below DMA100 and DMA200, or red flags (cash burn, debt or
   receivables piling up, pledges, auditor or governance issues). Exit.

RULES
- SME stocks (marked SME): the EBITDA margin, the net cash position (cash and investments minus borrowings) and cash
  from operating activities matter most. If the margin or the cash fell, the stock is tier 4 at best even when
  revenue and PAT grew (e.g. results that looked good, but margin and cash declined, and the stock fell to just above
  its DMA200).
- Judge growth on the latest period against the same period last year first, then against the previous period.
- A DMA shown as n/a has too little price history; judge on the others.
- Use only the data given. When something is missing, say so in "gaps" and don't assume it is good.
- Held stocks: action is one of "Add more", "Hold", "Trim", "Exit". Watchlist stocks: "Start position", "Watch",
  "Avoid". Tier 1–2 and a below-average weight → "Add more"; tier 5 → "Trim"; tier 6 → "Exit"."""

GRADE_PROMPT = """{role}

{rubric}

Grade each company below. Reply with one JSON object only:
{{"stocks": [{{"sid": "...", "tier": 1, "score": 0, "action": "...",
  "revenue": "up|flat|down|n/a", "ebitda_margin": "up|flat|down|n/a", "pat": "up|flat|down|n/a", "eps": "up|flat|down|n/a",
  "cash": "one short phrase on CFO / net cash / debt, or n/a",
  "visibility": "order book, capacity or guidance in one short phrase, or n/a",
  "technical": "above all DMAs | above long, below short | below long | ...",
  "positives": ["at most 3, with figures"], "negatives": ["at most 3, with figures"], "gaps": ["what was missing"],
  "why": "one sentence: why this tier and action"}}]}}
"score" is 0–100 within the tier (higher = stronger), so stocks in the same tier can be ordered.

COMPANIES
{companies}
"""

ROTATION_PROMPT = """{role}

Below is today's graded list (tier 1 best … 6 worst, weight = share of the money invested). Write the allocation
note. Reply with one JSON object only:
{{"headline": "one sentence on the day",
  "add_more": [{{"sid": "...", "why": "..."}}], "start": [{{"sid": "...", "why": "..."}}],
  "trim_or_exit": [{{"sid": "...", "why": "..."}}],
  "rotations": [{{"from": "sid", "to": "sid", "why": "move money from the weak one to the strong one"}}],
  "watch_next": ["what to check before acting, at most 5"]}}

{table}
"""


def _cfg(settings: dict) -> dict:
    return settings["priority"]


def _n(v, nd=1) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"


def _pct(a, b) -> str:
    return "n/a" if a is None or not b else f"{(a / b - 1) * 100:+.1f}%"


CASH_LINE = re.compile(
    r"net cash (flow )?(generated|from|used|inflow|outflow|\(used in\)|provided).{0,40}operat|cash generated from operations|"
    r"cash and cash equivalents|bank balances|current investments|borrowings|total equity|trade receivables", re.I)


def _cash_lines(doc) -> list[str]:
    """The cash-flow and balance-sheet rows of a results filing (tables are one row per line, columns `|`-separated)."""
    from .documents import page_texts

    try:
        pages = page_texts(ROOT / doc["path"], doc["sha256"])
    except Exception:
        return []
    out: list[str] = []
    for p in pages:
        for line in p.splitlines():
            line = " ".join(line.split())
            if CASH_LINE.search(line) and re.search(r"\d", line) and line not in out:
                out.append(line[:180])
    return out[:16]


def _quarters(conn, sid: str) -> str:
    rows = conn.execute("SELECT * FROM financials_q WHERE sid=? ORDER BY period_end DESC", (sid,)).fetchall()
    if not rows:
        return ""
    months = 3 if any(r["months"] == 3 for r in rows) else 6
    bases = [r["basis"] for r in rows if r["months"] == months]
    basis = max(set(bases), key=bases.count) if bases else None
    rs = [r for r in rows if r["months"] == months and r["basis"] == basis][:5][::-1]
    if not rs:
        return ""
    head = "| period end | revenue | EBITDA | margin % | PAT | EPS |"
    lines = [f"Reported ({basis}, {months}-month periods, ₹ Cr):", head] + [
        f"| {r['period_end']} | {_n(r['revenue_cr'])} | {_n(r['ebitda_cr'])} | "
        f"{_n(r['ebitda_cr'] / r['revenue_cr'] * 100 if r['ebitda_cr'] is not None and r['revenue_cr'] else None)} | "
        f"{_n(r['pat_cr'])} | {_n(r['eps'], 2)} |" for r in rs]
    return "\n".join(lines)


def candidates(conn, settings: dict) -> list[dict]:
    """Held and watched stocks with a filing in the window, each with everything the grader reads."""
    days = _cfg(settings)["days"]
    since = (now_ist().date() - timedelta(days=days)).isoformat()
    hold = {r["sid"]: dict(r) for r in conn.execute(
        "SELECT sid, SUM(invested) invested, SUM(quantity) qty FROM holdings WHERE sid IS NOT NULL AND quantity > 0 GROUP BY sid")}
    total = sum(h["invested"] or 0 for h in hold.values()) or 1
    stocks = conn.execute(
        f"SELECT s.*, t.close, t.dma20, t.dma40, t.dma50, t.dma100, t.dma200, t.hi52, t.lo52, t.phase, t.ret_1w, t.ret_1m, "
        f"t.ret_3m, t.volume, t.vol_avg20, t.d price_day FROM stocks s LEFT JOIN technicals t USING(sid) "
        f"WHERE (s.in_portfolio=1 OR s.in_watchlist=1) AND EXISTS (SELECT 1 FROM documents d WHERE d.sid=s.sid "
        f"AND d.backfill=0 AND d.status='extracted' AND d.kind IN ({','.join('?' * len(KINDS))}) "
        f"AND substr(d.filed_at, 1, 10) >= ?)", (*KINDS, since)).fetchall()
    out = []
    for s in stocks:
        sid = s["sid"]
        h = hold.get(sid)
        docs = conn.execute(
            f"SELECT * FROM documents WHERE sid=? AND backfill=0 AND status='extracted' AND kind IN ({','.join('?' * len(KINDS))}) "
            "AND substr(filed_at, 1, 10) >= ? ORDER BY filed_at", (sid, *KINDS, since)).fetchall()
        parts = [f"## {sid}: {s['name']}" + (" [SME]" if s["is_sme"] else "") + f" · sector {s['sector'] or 'n/a'}",
                 (f"Held: ₹{(h['invested'] or 0) / 1e5:,.1f} lakh invested, weight {(h['invested'] or 0) / total * 100:.1f}% of the "
                  f"portfolio" + (f", now worth ₹{h['qty'] * s['close'] / 1e5:,.1f} lakh" if s["close"] else ""))
                 if h else "Watchlist (not held)" + (f": {s['watch_reason']}" if s["watch_reason"] else "")]
        if s["close"]:
            parts.append(
                f"Price {s['price_day']}: close {_n(s['close'], 2)}; DMA40 {_n(s['dma40'], 2)} ({_pct(s['close'], s['dma40'])}), "
                f"DMA50 {_n(s['dma50'], 2)} ({_pct(s['close'], s['dma50'])}), DMA100 {_n(s['dma100'], 2)} "
                f"({_pct(s['close'], s['dma100'])}), DMA200 {_n(s['dma200'], 2)} ({_pct(s['close'], s['dma200'])}); "
                f"phase {s['phase'] or 'n/a'}; 1w {_n(s['ret_1w'])}%, 1m {_n(s['ret_1m'])}%, 3m {_n(s['ret_3m'])}%; "
                f"52w high {_n(s['hi52'], 2)} ({_pct(s['close'], s['hi52'])}), low {_n(s['lo52'], 2)}; "
                f"volume {_n((s['volume'] or 0) / s['vol_avg20'] if s['vol_avg20'] else None)}× its 20-day average")
        q = _quarters(conn, sid)
        if q:
            parts.append(q)
        rr = conn.execute("SELECT period, basis, numbers, review FROM results_reviews WHERE sid=? ORDER BY period_end DESC LIMIT 1",
                          (sid,)).fetchone()
        if rr:
            nums = json.loads(rr["numbers"] or "{}")
            n = nums.get("xbrl") or nums.get("pdf") or next(iter(nums.values()), {}) if nums else {}
            rv = json.loads(rr["review"] or "{}")
            keys = ("revenue_yoy_pct", "revenue_qoq_pct", "ebitda_margin_pct", "ebitda_margin_yoy_bp", "pat_yoy_pct",
                    "pat_qoq_pct", "eps")
            parts.append(f"Latest results review {rr['period']} ({rr['basis'] or ''}): "
                         + ", ".join(f"{k} {n.get(k)}" for k in keys if isinstance(n, dict) and n.get(k) is not None)
                         + (f"; verdict vs management's earlier targets: {rv['verdict']}" if rv.get("verdict") else "")
                         + "".join(f"\n- anomaly: {a.get('text')}" for a in (rv.get("anomalies") or [])[:3]))
        seen: set[str] = set()
        for d in docs:
            lines = json.loads(d["summary"] or "[]")
            parts.append(f"{d['kind'].replace('_', ' ').title()} filed {d['filed_at'][:10]} ({d['period'] or ''}):\n"
                         + "\n".join(f"- {x}" for x in lines))
            if d["kind"] in ("results", "press_release"):
                cash = [c for c in _cash_lines(d) if c not in seen]   # results and press release often share the tables
                seen.update(cash)
                if cash:
                    parts.append("Cash / debt rows from that filing (columns as printed):\n" + "\n".join(f"  {c}" for c in cash))
        ob = conn.execute("SELECT as_of, value_cr FROM order_book_history WHERE sid=? AND scope='total' AND verified > 0 "
                          "ORDER BY as_of DESC LIMIT 3", (sid,)).fetchall()
        if ob:
            parts.append("Order book: " + "; ".join(f"₹{r['value_cr']:,.0f} Cr on {r['as_of'][:10]}" for r in ob))
        facts = conn.execute(
            "SELECT f.section, f.text FROM doc_facts f JOIN documents d ON d.id=f.doc_id WHERE f.sid=? AND f.verified >= 0.5 "
            "AND f.section IN ('capex','tailwind','headwind','order_inflow','execution') AND substr(d.filed_at, 1, 10) >= ? "
            "ORDER BY f.id DESC LIMIT 8", (sid, since)).fetchall()
        if facts:
            parts.append("From the filings: " + "; ".join(f"[{r['section']}] {(r['text'] or '')[:140]}" for r in facts))
        g = conn.execute("SELECT metric, period, low, high, unit FROM guidance WHERE sid=? AND verified > 0 AND "
                         "(scope IS NULL OR scope='total') ORDER BY stated_on DESC, id DESC LIMIT 4", (sid,)).fetchall()
        if g:
            parts.append("Guidance: " + "; ".join(f"{r['metric']} {r['period'] or ''} {r['low']}–{r['high']} {r['unit'] or ''}" for r in g))
        out.append({"sid": sid, "name": s["name"], "held": bool(h), "sme": bool(s["is_sme"]),
                    "weight": round((h["invested"] or 0) / total * 100, 2) if h else 0.0,
                    "close": s["close"], "block": "\n".join(parts)[:6000],
                    "docs": [f"{d['kind']} {d['filed_at'][:10]}" for d in docs]})
    return out


def _call(conn, settings: dict, prompt: str, purpose: str, max_tokens: int) -> dict:
    from .extract import llm_json

    client = _cfg(settings)["client"]
    return llm_json(conn, {**settings, "llm": {**settings["llm"], "client": client}}, prompt, purpose=purpose, sid=None,
                    max_tokens=max_tokens, cache_name=f"priority_{hashlib.sha256(prompt.encode()).hexdigest()[:24]}",
                    system="You are a disciplined small-cap momentum investor. Output strict JSON only.")


def _grade(settings: dict, batch: list[dict]) -> list[dict]:
    prompt = GRADE_PROMPT.format(role=ROLE, rubric=RUBRIC, companies="\n\n".join(c["block"] for c in batch))
    with session() as conn:
        data = _call(conn, settings, prompt, "priority", 16000)
    by = {str(g.get("sid")): g for g in data.get("stocks") or [] if isinstance(g, dict)}
    return [{**c, **(by.get(c["sid"]) or {"tier": None, "why": "not graded (the model left it out)"})} for c in batch]


def _cell(x) -> str:
    return str(x if x is not None else "").replace("|", "/").replace("\n", " ")


def _disp(g: dict) -> str:
    """BSE-only stocks are known by a scrip code: show the name with it."""
    return f"{g['sid']} {g['name']}" if g["sid"].isdigit() and g.get("name") else g["sid"]


def run(settings: dict) -> dict:
    cfg = _cfg(settings)
    with session() as conn:
        cands = candidates(conn, settings)
    if not cands:
        return {"stocks": 0}
    batches = [cands[i:i + cfg["batch"]] for i in range(0, len(cands), cfg["batch"])]
    graded: list[dict] = []
    failed = 0
    with cf.ThreadPoolExecutor(cfg["workers"]) as ex:
        for fut in [ex.submit(_grade, settings, b) for b in batches]:
            try:
                graded += fut.result()
            except Exception as exc:
                failed += 1
                print(f"priority batch failed: {exc.__class__.__name__}: {exc}")
    graded.sort(key=lambda g: (g.get("tier") or 9, -(g.get("score") or 0), not g["held"]))
    rows = ["| # | Stock | Held | Weight % | Tier | Score | Action | Rev | Margin | PAT | EPS | Cash | Visibility | Technical | Why |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, g in enumerate(graded, 1):
        rows.append(f"| {i} | {_disp(g)}{' (SME)' if g['sme'] else ''} | {'yes' if g['held'] else 'watch'} | "
                    f"{g['weight']:.1f} | {_cell(g.get('tier'))} | {_cell(g.get('score'))} | **{_cell(g.get('action'))}** | "
                    f"{_cell(g.get('revenue'))} | {_cell(g.get('ebitda_margin'))} | {_cell(g.get('pat'))} | {_cell(g.get('eps'))} | "
                    f"{_cell(g.get('cash'))} | {_cell(g.get('visibility'))} | {_cell(g.get('technical'))} | {_cell(g.get('why'))} |")
    table = "\n".join(rows)
    compact = "\n".join(f"{g['sid']} | {'held' if g['held'] else 'watch'} | weight {g['weight']:.1f}% | tier {g.get('tier')} | "
                        f"score {g.get('score')} | {g.get('action')} | {g.get('why')}" for g in graded)
    try:
        with session() as conn:
            note = _call(conn, settings, ROTATION_PROMPT.format(role=ROLE, table=compact), "priority", 8000)
    except Exception as exc:
        note = {"headline": f"(allocation note failed: {exc.__class__.__name__})"}
    today = now_ist().date()
    held_w = {g["sid"]: g for g in graded}

    def lst(key: str, title: str) -> list[str]:
        items = note.get(key) or []
        return [f"### {title}", ""] + ([f"- **{_disp(held_w[x['sid']]) if x.get('sid') in held_w else x.get('sid')}**"
                                        + (f" ({held_w[x['sid']]['weight']:.1f}% now)"
                                                                     if x.get("sid") in held_w and held_w[x['sid']]["held"] else "")
                                        + f": {x.get('why')}" for x in items if isinstance(x, dict)] or ["- none"]) + [""]

    lines = [f"# Priority list: {today.strftime('%a %d %b %Y')}", "",
             f"{len(graded)} held and watched stocks with a results, press release, presentation or concall filing in the "
             f"last {cfg['days']} days, graded by {cfg['client']} as a small-cap momentum investor (tier 1 = deserves "
             f"the most money … 6 = exit). Stocks without a filing in that window are not ranked.", "",
             f"**{note.get('headline', '')}**", ""]
    lines += lst("add_more", "Invest more (held)") + lst("start", "Start a position (watchlist)") + \
        lst("trim_or_exit", "Trim or exit")
    def name(sid) -> str:
        return _disp(held_w[sid]) if sid in held_w else str(sid)

    lines += ["### Rotations", ""] + ([f"- {name(r.get('from'))} → {name(r.get('to'))}: {r.get('why')}" for r in note.get("rotations") or []
                                       if isinstance(r, dict)] or ["- none"]) + [""]
    if note.get("watch_next"):
        lines += ["### Check before acting", ""] + [f"- {x}" for x in note["watch_next"]] + [""]
    lines += ["## Full list, best first", "", table, "", "## Details", ""]
    for g in graded:
        lines += [f"### {g['sid']}: {g['name']} (tier {g.get('tier')}, {g.get('action')})", "",
                  "Filings: " + ", ".join(g["docs"])]
        for k, t in (("positives", "+"), ("negatives", "−"), ("gaps", "?")):
            lines += [f"- {t} {x}" for x in g.get(k) or []]
        lines.append("")
    if failed:
        lines += [f"_{failed} batch(es) failed; their stocks are missing above._", ""]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{today.strftime('%b').lower()}-{today.day}-priority.md"
    path.write_text("\n".join(lines) + "\n")
    return {"stocks": len(graded), "failed_batches": failed, "report": str(path),
            "add_more": [x.get("sid") for x in note.get("add_more") or [] if isinstance(x, dict)],
            "trim_or_exit": [x.get("sid") for x in note.get("trim_or_exit") or [] if isinstance(x, dict)]}
