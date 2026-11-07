"""
The valuation memo: the investor's own analyst prompt, run per company over everything the tracker holds
(model actuals and baseline estimates, guidance, order book and its execution period, capex, operating
KPIs, macro / policy facts read from filings, the notes timeline and the investor's own note).

The memo returns the prompt's sections — the commitment & pipeline tracker, macro & regulatory
transmission, the 3- and 5-year forward matrix, the risk dashboard — plus structured projections for the
next eight periods and three fiscal years and bear / base / bull multiples. model.py lays the projections
over its own rule-based estimates (each figure checked for scale) and recomputes every price as EPS ×
multiple, so the arithmetic on the page is never the model's.

A memo is rewritten when a newer results filing, concall note, order-book figure, guidance or capex fact
arrives for the company (or after `valuation.max_age_days`).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta

from .util import now_ist

ANALYST_PROMPT = """Role: Senior Institutional Equity Research Analyst specializing in Small-Caps and Micro-Caps.
Task: Conduct a deep-dive, forward-looking valuation analysis for {company}. Your analysis must completely bypass
generic definitions and trailing metrics. Instead, focus entirely on mapping management's stated execution runway
against prevailing macroeconomic cycles. Structure your research into these sections:
1. THE MANAGEMENT COMMITMENT & REVENUE PIPELINE TRACKER
- Extract and synthesize explicit volume, capacity, or utilization guidance from the latest quarterly earnings calls or
  investor presentations.
- Quantify the active Order Book, upcoming deals, and global/domestic corporate partnerships. Detail how these convert
  into near-term billing.
- Evaluate upcoming CapEx or capacity expansions (e.g., new plants, retail store scaling, or digital platform rollouts).
  Provide a clear timeline for when these assets start producing revenue.
2. MACROECONOMIC & REGULATORY TRANSMISSION VULNERABILITIES
- Detail how the current Macro Environment impacts this specific business model. Analyze the simultaneous interplay of:
  Interest Rates & Debt Financing Costs (balance sheet leverage and working capital strain); Global and Domestic
  Inflation (can the company pass on raw material/freight spikes, or will margins compress?); Currency Fluctuations &
  Dollar Index (export-driven USD beneficiary or import-dependent player?).
- Assess policy shifts, production-linked incentives (PLI), import/export tariffs, and Free Trade Agreements (FTAs)
  acting as direct tailwinds or structural headwinds.
3. THE 3-YEAR & 5-YEAR FORWARD VALUATION MATRIX
- Build a mathematical year-by-year forward projection model assuming a normal execution cycle.
- Project the specific target milestones for: Revenue, Operating EBITDA Margin %, Net Profit (PAT), and Forward EPS.
- Assign a justified Target P/E or EV/EBITDA multiple. Ground this multiple in the company's forward Return on Equity
  (ROE/ROCE) and execution risk, rather than speculative sector hype.
- Calculate the resulting Implied Intrinsic Market Cap and estimated Share Price for both the 3-year and 5-year
  horizons. Outline a clear risk-mitigation strategy to minimize entry slippage.
5. THE CRITICAL RISK DASHBOARD & EXIT TRIGGERS
- List 3 hyper-specific operational or financial red flags that would completely invalidate your forward model (e.g.,
  raw material spread contraction, execution delays, or promoter pledging shifts).
- Define the exact "Expensive/Overvalued" threshold multiple or price target.
Tone & Style: authoritative, professional and candid. Be precise with percentages, numbers, and timeline tracking.
Avoid generic advice.

Work ONLY from the data below (it is what the investor's tracker holds; say when something is missing rather than
inventing it). The TRACKER BASELINE is mechanical (guidance, else order book, else trend, split by past seasonality):
challenge it — check every projected period against the latest reported periods, the year-to-date run-rate, the order
book's execution and stated timing of new capacity; when the run-rate already beats a guidance, say so and project
from the run-rate. Bear, base and bull must differ in revenue, margin and EPS, not only in the multiple. Money in ₹ crore. Fiscal years end in March ({fy_curr} = Apr {fy_start}–Mar {fy_end}). Section 4 (portfolio
allocation) is out of scope: give valuation levels, not position sizes.

Return ONE JSON object with exactly these keys:
{{
  "one_line": "the investment case in ≤ 25 words",
  "pipeline": {{"summary": ["3-6 lines, numbers first"],
               "guidance": [{{"metric": "...", "period": "{fy_curr}", "value": "...", "source": "where it was said"}}],
               "order_book": {{"value_cr": number|null, "as_of": "YYYY-MM-DD"|null, "execution_months": number|null,
                              "billing_by_period": [{{"period": "{next_label}", "revenue_cr": number}}], "note": "..."}},
               "capex": [{{"what": "...", "amount_cr": number|null, "capacity": "...", "revenue_from": "{fy_next}",
                          "peak_revenue_cr": number|null}}],
               "deals": ["partnerships / large deals, with values"]}},
  "macro": {{"summary": ["3-5 lines"], "rates": "...", "inflation": "...", "currency": "...",
            "policy": ["PLI / tariffs / FTAs and their direction"]}},
  "projections": {{
     "periods": [{{"period": "{next_label} …", "revenue_cr": number, "ebitda_margin_pct": number, "pat_cr": number}}],
     "years": [{{"fy": "{fy_curr}", "revenue_cr": number, "ebitda_margin_pct": number, "pat_cr": number, "eps": number}}],
     "assumptions": ["how revenue, margin and PAT were derived, each with its source in the data"]}},
  "valuation": {{"metric": "P/E|EV/EBITDA", "roe_pct": number|null, "multiple_rationale": "...",
                "bear": {{"fy": "{fy_next}", "eps": number, "multiple": number, "rationale": "..."}},
                "base": {{"fy": "{fy_next}", "eps": number, "multiple": number, "rationale": "..."}},
                "bull": {{"fy": "{fy_next}", "eps": number, "multiple": number, "rationale": "..."}},
                "three_year": {{"fy": "FY{y3}", "eps": number, "multiple": number, "market_cap_cr": number}},
                "five_year": {{"fy": "FY{y5}", "eps": number, "multiple": number, "market_cap_cr": number}},
                "entry_slippage": "how to stage entries to limit slippage"}},
  "risks": {{"red_flags": ["exactly 3, hyper-specific"], "expensive_above": {{"multiple": number, "price": number, "why": "..."}},
            "exit_triggers": ["..."]}}
}}
"projections.periods" covers the next {n_periods} {period_word} after the last reported one ({periods_list}); "years" covers
{years_list}. Use the order book's execution and any stated distribution of orders across quarters for the period split.

COMPANY: {company} ({sid}) · sector: {sector} · {listing}
PRICE: ₹{close} · market cap ₹{mcap} Cr · shares {shares} crore · trailing P/E {pe_ttm} · own 3-year P/E band {pe_band} ·
sector P/E median {sector_pe}

{data}"""


def _f(v, nd=1):
    return "n/a" if v is None else (f"{v:,.{nd}f}" if isinstance(v, (int, float)) else str(v))


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    lines = ["| ₹ Cr | " + " | ".join(r["label"] for r in rows) + " |"]
    for key, name in cols:
        lines.append(f"| {name} | " + " | ".join(_f(r.get(key), 2 if key == "eps" else 1) for r in rows) + " |")
    return "\n".join(lines)


def context(conn: sqlite3.Connection, s, settings: dict) -> tuple[dict, str]:
    from . import company_notes, model, score

    h = model.history(conn, s)
    est = model.estimates(conn, s, h, settings)
    acts = [p for p in h["periods"] if p["sales"] is not None][-8:]
    cols = [("sales", "Revenue"), ("ebitda", "EBITDA"), ("opm", "EBITDA margin %"), ("pat", "PAT"), ("eps", "EPS"),
            ("sales_yoy", "Revenue YoY %")]
    parts = ["REPORTED PERIODS (₹ Cr)\n" + _table(acts, cols) if acts else "REPORTED PERIODS: none",
             "FISCAL YEARS (₹ Cr)\n" + _table(h["annual"], cols[:5]) if h["annual"] else ""]
    if est.get("available"):
        parts.append("TRACKER BASELINE ESTIMATES (rule-based; improve them with the data below)\n"
                     + _table(est["periods"], cols[:5]) + "\n" + _table(est["years"], cols[:5])
                     + "\nBaseline notes: " + "; ".join(est["notes"]))
    ob = conn.execute("SELECT as_of, value_cr, quote FROM order_book_history WHERE sid=? AND scope='total' AND verified > 0 "
                      "ORDER BY as_of DESC LIMIT 6", (s["sid"],)).fetchall()
    if ob:
        parts.append("ORDER BOOK (latest first)\n" + "\n".join(f"- {r['as_of'][:10]}: ₹{r['value_cr']:,.0f} Cr — “{(r['quote'] or '')[:160]}”" for r in ob))
    facts = conn.execute("SELECT section, metric, period, value, unit, text, quote FROM doc_facts WHERE sid=? AND verified >= 0.5 "
                         "AND section IN ('execution','order_inflow','capex','tailwind','headwind','policy','interest_rate') "
                         "ORDER BY id DESC LIMIT 30", (s["sid"],)).fetchall()
    if facts:
        parts.append("FROM FILINGS (latest first)\n" + "\n".join(
            f"- [{r['section']}] {r['text'] or r['metric'] or ''} {('' if r['value'] is None else _f(r['value']) + ' ' + (r['unit'] or ''))} "
            f"{('(' + r['period'] + ')') if r['period'] else ''} — “{(r['quote'] or '')[:140]}”" for r in facts))
    guid = score._guidance(conn, s["sid"])
    if guid:
        parts.append("GUIDANCE (latest per metric)\n" + "\n".join(
            f"- {k}: {g.get('period') or ''} {g.get('low')}–{g.get('high')} {g.get('unit') or ''} — “{(g.get('quote') or '')[:160]}”"
            for k, g in guid.items()))
    view = conn.execute("SELECT view FROM company_view WHERE sid=?", (s["sid"],)).fetchone()
    if view:
        v = json.loads(view["view"] or "{}")
        parts.append("NOTES VIEW\n" + json.dumps({k: v.get(k) for k in ("what_it_does", "story", "triggers", "delivery", "trend",
                                                                        "sector_drivers", "risks")}, ensure_ascii=False)[:3000])
    notes = conn.execute("SELECT period, note FROM company_notes WHERE sid=? ORDER BY period_end DESC LIMIT 2", (s["sid"],)).fetchall()
    for n in notes:
        parts.append(f"QUARTER NOTE {n['period']}\n{(n['note'] or '')[:2500]}")
    commits = company_notes.open_commitments(conn, s["sid"])
    if commits:
        parts.append("OPEN COMMITMENTS\n" + "\n".join(f"- [{c['said_in']}] {c['what']}" + (f" — target {c['target']}" if c.get("target") else "")
                                                     for c in commits))
    k = model.kpis(s, limit=8)
    if k["metrics"]:
        parts.append("OPERATING KPIs FROM CONCALLS\n" + "\n".join(
            f"- {m['name']}: " + "; ".join(f"{q} {c['t'][:60]}" for q, c in m["cells"].items()) for m in k["metrics"]))
    mine = [x for x in (s["watch_reason"],) if x]
    mine += [f"{r['d']} · {r['stance']} · {r['title']}" for r in conn.execute(
        "SELECT n.d, m.stance, n.title FROM note_mentions m JOIN notes n ON n.id=m.note_id WHERE m.sid=? ORDER BY n.d DESC LIMIT 3",
        (s["sid"],))]
    if mine:
        parts.append("THE INVESTOR'S OWN NOTES\n" + "\n".join(f"- {x}" for x in mine))
    return {"h": h, "est": est}, "\n\n".join(p for p in parts if p)


def prompt_for(conn: sqlite3.Connection, s, settings: dict) -> tuple[str, dict]:
    from . import model

    ctx, data = context(conn, s, settings)
    h, est = ctx["h"], ctx["est"]
    close = s["close"] if "close" in s.keys() else None
    sh = model.shares_cr(conn, s["sid"], h)
    acts = [p for p in h["periods"] if p["eps"] is not None]
    per = h["per_year"]
    eps_ttm = sum(p["eps"] for p in acts[-per:]) if len(acts) >= per else None
    band = model.pe_band(conn, s, h)
    sec = model.sector_pe(conn, s["sector"])
    nxt = [p["label"] for p in (est.get("periods") or [])]
    yrs = [y["label"].rstrip("E") for y in (est.get("years") or [])]
    fy0 = int(yrs[0][2:]) if yrs else (now_ist().year % 100 + 1)
    fy_curr = f"FY{fy0}"
    fy_next = f"FY{fy0 + 1}"
    fy_start = 2000 + fy0 - 1
    fy_end = 2000 + fy0
    p = ANALYST_PROMPT.format(
        company=s["name"] or s["sid"], sid=s["sid"], sector=s["sector"] or "n/a",
        listing="BSE" if not s["nse_symbol"] else ("NSE SME" if s["is_sme"] else "NSE"),
        close=_f(close), mcap=_f(close * sh if close and sh else None, 0), shares=_f(sh, 3),
        pe_ttm=_f(close / eps_ttm if close and eps_ttm and eps_ttm > 0 else None),
        pe_band=f"{band.get('p25')}–{band.get('p75')} (median {band.get('median')})" if band.get("median") else "n/a",
        sector_pe=f"{sec.get('median')} ({sec.get('n')} peers)" if sec.get("median") else "n/a",
        next_label=nxt[0] if nxt else "next period", n_periods=len(nxt) or 2 * per,
        period_word="half-years" if h["half"] else "quarters", periods_list=", ".join(nxt) or "n/a",
        years_list=", ".join(yrs) + f" and FY{fy0 + 4} (5-year)" if yrs else "the next three fiscal years",
        fy_curr=fy_curr, fy_next=fy_next, fy_start=fy_start, fy_end=fy_end,
        y3=fy0 + 2, y5=fy0 + 4, data=data)
    return p, ctx


def write_memo(conn: sqlite3.Connection, s, settings: dict) -> dict | None:
    from . import extract

    prompt, _ = prompt_for(conn, s, settings)
    key = hashlib.sha256(prompt.encode()).hexdigest()[:24]
    out = None
    client = settings["llm"]["client"]
    fallback = settings.get("valuation", {}).get("fallback_client")
    # gpt-4.1 sometimes loops on this long JSON (one object's keys over and over until max_tokens): retry with a
    # frequency penalty, then hand it to the fallback model, which hasn't looped on it
    attempts = [(client, 0, 0, 7000), (client, 0.4, 0.3, 7000)] + ([(fallback, 0, 0, 12000)] if fallback else [])
    for i, (name, temp, fp, max_tokens) in enumerate(attempts):
        try:
            out = extract.llm_json(conn, {**settings, "llm": {**settings["llm"], "client": name}}, prompt,
                                   purpose="valuation", sid=s["sid"], max_tokens=max_tokens,
                                   cache_name=f"valuation_{s['sid']}_{key}" + ("" if name == client else f"_{name}"),
                                   system="You are a senior equity research analyst. Output strict, complete JSON only; "
                                          "keep each list to at most 5 short items.",
                                   temperature=temp, frequency_penalty=fp)
            client = name
            break
        except json.JSONDecodeError:
            if i == len(attempts) - 1:
                raise
    if not isinstance(out, dict) or not out.get("projections"):
        return None
    conn.execute("INSERT INTO valuations(sid, created_at, model, inputs_hash, memo) VALUES(?,?,?,?,?)",
                 (s["sid"], now_ist().isoformat(timespec="seconds"), client, key,
                  json.dumps(out, ensure_ascii=False)))
    conn.execute("DELETE FROM kv WHERE k=?", (f"valuation_stale:{s['sid']}",))
    return out


def latest(conn: sqlite3.Connection, sid: str) -> dict | None:
    r = conn.execute("SELECT created_at, memo FROM valuations WHERE sid=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    if not r:
        return None
    try:
        m = json.loads(r["memo"])
    except ValueError:
        return None
    return m | {"_created_at": r["created_at"]} if isinstance(m, dict) else None


def public(memo: dict) -> dict:
    return {k: memo.get(k) for k in ("one_line", "pipeline", "macro", "valuation", "risks")} | {
        "assumptions": (memo.get("projections") or {}).get("assumptions") or [], "created_at": memo.get("_created_at")}


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip()) if v not in (None, "") else None
    except ValueError:
        return None


def apply(memo: dict, est: dict, h: dict) -> dict:
    """Lay the memo's projections over the rule-based estimates, period by period, keeping each check in sight."""
    from .results import norm_period

    sh, conv = est.get("shares_cr"), est.get("conversion")
    proj = memo.get("projections") or {}
    out = {**est, "source": "memo", "notes": list(est["notes"]), "memo_at": memo.get("_created_at")}

    def lay(row: dict, m: dict) -> dict:
        rev, mg, pat = _num(m.get("revenue_cr")), _num(m.get("ebitda_margin_pct")), _num(m.get("pat_cr"))
        if rev is None or rev <= 0 or (row["sales"] and not (0.33 <= rev / row["sales"] <= 3.0)):
            return row | {"check": f"memo revenue {rev} ignored (out of scale)"} if rev is not None else row
        mg = mg if mg is not None and -50 < mg < 90 else row.get("opm")
        ebitda = rev * mg / 100 if mg is not None else None
        if pat is not None and row.get("pat") and row["pat"] > 0 and row.get("sales") and \
                not (0.33 <= pat / (row["pat"] * rev / row["sales"]) <= 3):
            pat = None                         # the memo's profit is out of line with its own revenue: use the conversion
        if pat is None and ebitda is not None and conv is not None:
            pat = ebitda * conv
        eps = _num(m.get("eps"))
        if pat is not None and sh:
            eps = pat / sh                     # EPS always on the share count, never the model's own division
        return row | {"sales": round(rev, 1), "opm": round(mg, 1) if mg is not None else None,
                      "ebitda": round(ebitda, 1) if ebitda is not None else None,
                      "pat": round(pat, 1) if pat is not None else row.get("pat"),
                      "eps": round(eps, 2) if eps is not None else row.get("eps"), "source": "memo"}
    by_p = {norm_period(str(m.get("period"))) or str(m.get("period")): m for m in proj.get("periods") or [] if isinstance(m, dict)}
    out["periods"] = [lay(p, by_p[p["label"]]) if p["label"] in by_p else p for p in est["periods"]]
    by_y = {str(m.get("fy")).upper().rstrip("E"): m for m in proj.get("years") or [] if isinstance(m, dict)}
    out["years"] = [lay(y, by_y[y["label"].rstrip("E")]) if y["label"].rstrip("E") in by_y else y for y in est["years"]]
    prev = {a["fy"]: a for a in h["annual"]}
    for i, y in enumerate(out["years"]):
        base = out["years"][i - 1] if i else prev.get(y["fy"] - 1)
        y["sales_yoy"] = _pct(y["sales"], (base or {}).get("sales"))
        y["pat_yoy"] = _pct(y["pat"], (base or {}).get("pat"))
    acts = {(p["fy"], p["idx"]): p for p in h["periods"]}
    for p in out["periods"]:
        prev_p = acts.get((p["fy"] - 1, p["idx"])) or next((q for q in out["periods"] if q["fy"] == p["fy"] - 1 and q["idx"] == p["idx"]), None)
        p["sales_yoy"] = _pct(p["sales"], (prev_p or {}).get("sales"))
        p["pat_yoy"] = _pct(p["pat"], (prev_p or {}).get("pat"))
    out["notes"].insert(0, "Estimates from the valuation memo of " + (memo.get("_created_at") or "")[:10]
                        + "; rule-based figures kept where the memo is silent or out of scale")
    return out


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None


def scenarios_from(memo: dict, est: dict, close: float | None, model_cases: dict | None = None) -> dict | None:
    v = memo.get("valuation") or {}
    if not all(isinstance(v.get(k), dict) for k in ("bear", "base", "bull")):
        return None
    if model_cases is not None and not model_cases.get("available"):
        return None                            # earnings too small for P/E: the memo's multiples don't apply either
    sh = est.get("shares_cr")
    out = {"available": True, "source": "memo", "metric": v.get("metric") or "P/E", "fy": (v["base"].get("fy") or ""),
           "pe_basis": {"base": v.get("multiple_rationale"), "roe_pct": v.get("roe_pct")}}
    same_eps = len({_num(v[k].get("eps")) for k in ("bear", "base", "bull")}) == 1
    model_sc = model_cases or {}
    # the table's EPS for that year anchors the cases (the memo's own valuation EPS can drift from its projections)
    fy = str(v["base"].get("fy") or "").upper().rstrip("E")
    yr = next((y for y in est.get("years") or [] if y["label"].rstrip("E") == fy), None)
    m_base = _num(v["base"].get("eps"))
    anchor = (yr["eps"] / m_base) if yr and yr.get("eps") and m_base and m_base > 0 else 1.0
    for k in ("bear", "base", "bull"):
        c = v[k]
        eps, mult = _num(c.get("eps")), _num(c.get("multiple"))
        if same_eps and (model_sc.get(k) or {}).get("eps") is not None and k != "base":
            # the memo varied only the multiple: bear / bull EPS move revenue and margin as the model's cases do
            eps = round(eps * model_sc[k]["eps"] / model_sc["base"]["eps"], 2) if eps and model_sc["base"].get("eps") else eps
        eps = round(eps * anchor, 2) if eps is not None else None
        t_eps = (model_cases or {}).get("trailing_eps")
        if eps is not None and t_eps and t_eps > 0 and eps > 3 * t_eps:
            eps = round(3 * t_eps, 2)          # the same bound as the rule: at most 3× trailing EPS in two years
        cur = (model_cases or {}).get("pe_basis", {}).get("current")
        if mult and cur and k == "base":
            mult = max(cur * 0.6, min(cur * 2.0, mult))   # the memo's multiple, within reason of today's P/E
        price = eps * mult if eps and mult and eps > 0 and (out["metric"] or "").upper().startswith("P") else _num(c.get("price"))
        out[k] = {"eps": round(eps, 2) if eps is not None else None, "pe": mult, "price": round(price, 1) if price else None,
                  "upside_pct": round((price / close - 1) * 100, 1) if price and close else None,
                  "rationale": c.get("rationale"), "pat": round(eps * sh, 1) if eps is not None and sh else None}
    for k in ("three_year", "five_year"):
        c = v.get(k) or {}
        eps, mult = _num(c.get("eps")), _num(c.get("multiple"))
        price = eps * mult if eps and mult and eps > 0 else None
        out[k] = {"fy": c.get("fy"), "eps": eps, "pe": mult, "price": round(price, 1) if price else None,
                  "market_cap_cr": round(price * sh, 0) if price and sh else _num(c.get("market_cap_cr")),
                  "upside_pct": round((price / close - 1) * 100, 1) if price and close else None}
    exp = (memo.get("risks") or {}).get("expensive_above") or {}
    out["expensive_above"] = {"pe": _num(exp.get("multiple")), "price": _num(exp.get("price")), "why": exp.get("why")}
    return out


# ── when to rewrite ─────────────────────────────────────────────────────────

def stale(conn: sqlite3.Connection, sid: str, settings: dict) -> str | None:
    """Why the latest memo is out of date (None when it is current)."""
    r = conn.execute("SELECT created_at FROM valuations WHERE sid=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    if not r:
        return "no memo yet"
    at = r["created_at"]
    max_age = int(settings.get("valuation", {}).get("max_age_days", 120))
    if datetime.fromisoformat(at) < now_ist() - timedelta(days=max_age):
        return f"older than {max_age} days"
    for q, why in (("SELECT 1 FROM results_reviews WHERE sid=? AND updated_at > ?", "new results"),
                   ("SELECT 1 FROM company_notes WHERE sid=? AND created_at > ?", "new concall / presentation note"),
                   ("SELECT 1 FROM order_book_history h JOIN documents d ON d.id=h.doc_id WHERE h.sid=? AND d.updated_at > ? "
                    "AND d.backfill=0", "new order-book figure"),
                   ("SELECT 1 FROM guidance g JOIN documents d ON d.id=g.doc_id WHERE g.sid=? AND d.updated_at > ? AND d.backfill=0",
                    "new guidance"),
                   ("SELECT 1 FROM doc_facts f JOIN documents d ON d.id=f.doc_id WHERE f.sid=? AND d.updated_at > ? "
                    "AND d.backfill=0 AND f.section IN ('capex','order_inflow')", "new capex / order-inflow fact")):
        try:
            if conn.execute(q, (sid, at)).fetchone():
                return why
        except sqlite3.OperationalError:
            continue
    return None


def refresh(conn: sqlite3.Connection, settings: dict, deadline=lambda: True, limit: int = 2, sids: list[str] | None = None) -> dict:
    """Rewrite stale memos for held and watched companies (held first)."""
    from . import extract

    rows = conn.execute("SELECT s.*, t.close FROM stocks s LEFT JOIN technicals t USING(sid) "
                        "WHERE (s.in_portfolio=1 OR s.in_watchlist=1) ORDER BY s.in_portfolio DESC, s.sid").fetchall()
    out = {"written": 0, "failed": 0, "deferred_budget": False}
    for s in rows:
        if out["written"] + out["failed"] >= limit or not deadline():
            break
        if sids is not None and s["sid"] not in sids:
            continue
        why = stale(conn, s["sid"], settings)
        if not why or (why == "no memo yet" and not conn.execute(
                "SELECT 1 FROM kv WHERE k='valuation_batch_done'").fetchone()):
            continue                     # first memos come from the one-off batch (`python -m tracker valuations --all`)
        if not extract.can_spend(conn, settings, 9000, 3500, "valuation"):
            out["deferred_budget"] = True
            break
        try:
            ok = write_memo(conn, s, settings)
            conn.commit()
            out["written" if ok else "failed"] += 1
        except extract.BudgetExceeded:
            out["deferred_budget"] = True
            break
        except Exception as exc:
            conn.rollback()
            out["failed"] += 1
            print(f"valuation {s['sid']}: {exc.__class__.__name__}: {exc}")
    return out


def run_batch(all_: bool = False, sids: list[str] | None = None, workers: int = 4) -> dict:
    """Write memos for held and watched companies (held first) — every one with `all_`, else only stale ones —
    `workers` at a time, each on its own connection; stops when the day's budget is spent."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from . import extract
    from .config import load_settings
    from .db import session

    settings = load_settings()
    with session() as conn:
        rows = conn.execute("SELECT s.sid FROM stocks s WHERE (s.in_portfolio=1 OR s.in_watchlist=1) "
                            "ORDER BY s.in_portfolio DESC, s.sid").fetchall()
        todo = [r["sid"] for r in rows if (sids is None or r["sid"] in sids)
                and (all_ or sids is not None or stale(conn, r["sid"], settings))]
    lock = threading.Lock()
    out = {"queued": len(todo), "written": 0, "failed": 0, "skipped_budget": 0}
    stop = threading.Event()

    def one(sid: str) -> None:
        if stop.is_set():
            return
        with session() as conn:
            if not extract.can_spend(conn, settings, 9000, 3500, "valuation"):
                stop.set()
                with lock:
                    out["skipped_budget"] += 1
                return
            s = conn.execute("SELECT s.*, t.close FROM stocks s LEFT JOIN technicals t USING(sid) WHERE s.sid=?", (sid,)).fetchone()
            try:
                ok = write_memo(conn, s, settings)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                ok = None
                print(f"valuation {sid}: {exc.__class__.__name__}: {exc}", flush=True)
        with lock:
            out["written" if ok else "failed"] += 1
            n = out["written"] + out["failed"]
        print(f"[{n}/{len(todo)}] {sid}: {'ok' if ok else 'failed'}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, todo))
    if all_ and not stop.is_set():
        with session() as conn:
            conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES('valuation_batch_done', ?, ?)",
                         (f'"{now_ist().date().isoformat()}"', now_ist().isoformat(timespec="seconds")))
            conn.commit()
    return out
