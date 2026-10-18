"""
LLM extraction of order book, orders, guidance, capex and macro commentary from one prepared
document (documents.py), via the Azure OpenAI client in llm/client.py.

Cost controls: only `prepared` documents (tracked stocks, keyword-relevant), only their relevant
pages trimmed to llm.max_input_tokens, one call per document (cached by PDF hash upstream), a
daily token budget with a separate cap for back-fill, every call logged in llm_calls.

Trust: every figure must carry a verbatim quote and its page. Quotes are checked against the
page text — 1 = found verbatim (and the number is in it), 0.5 = near match or number not in the
quote, 0 = not found. Unverified figures are kept but flagged, and left out of growth maths.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime

from .config import ROOT, TRACKER_DIR
from .db import add_event
from .documents import file_link, page_texts, relevant_pages
from .util import now_ist

SCHEMA_HINT = """{
  "document": {"type": "investor_presentation|earnings_call_transcript|results|order_win|press_release|other",
               "period": "quarter or year the document reports on, e.g. Q2 FY27", "consolidated": true|false|null},
  "order_book": [{"value_cr": number, "as_of": "YYYY-MM-DD", "scope": "total | segment name", "quote": "...", "page": n}],
  "order_inflow": [{"value_cr": number, "period": "Q2 FY27 | H1 FY27 | FY26", "quote": "...", "page": n}],
  "execution_period_months": {"value": number, "quote": "...", "page": n} | null,
  "orders": [{"customer": "...", "order_type": "supply | EPC | services | export | O&M | ...", "value_cr": number|null,
              "duration_months": number|null, "date": "YYYY-MM-DD"|null, "description": "short", "quote": "...", "page": n}],
  "guidance": [{"metric": "revenue|revenue_growth|ebitda_margin|pat|pat_growth|eps|volume|order_inflow|capex|other",
                "scope": "total | segment or business name", "period": "FY27 | H2 FY27 | FY28 ...", "low": number|null, "high": number|null,
                "unit": "cr|pct|x|units", "text": "short paraphrase", "quote": "...", "page": n}],
  "capex": [{"what": "...", "amount_cr": number|null, "capacity": "..."|null, "timeline": "..."|null, "quote": "...", "page": n}],
  "tailwinds": [{"text": "...", "quote": "...", "page": n}],
  "headwinds": [{"text": "...", "quote": "...", "page": n}],
  "policy": [{"text": "government policy / regulation and its effect", "quote": "...", "page": n}],
  "interest_rate": [{"text": "sensitivity to interest rates / financing costs", "quote": "...", "page": n}]
}"""

PROMPT = """You extract facts from an Indian listed company's exchange filing for an investor's tracker.
Company: {company} ({sid}). Document: {kind}, filed {filed}. Pages are marked "=== Page N ===".

Return ONE JSON object with exactly these keys (use [] or null when the document does not say):
{schema}

Rules:
- Only facts the document states explicitly. Never estimate, infer or compute a figure that is not written.
- "quote" is copied character-for-character from the page text (one or two sentences, max 300 characters)
  and must contain the number it supports. "page" is the N of the page the quote is on.
- Convert money to ₹ crore: 1 crore = 10 million; ₹ X million (mn) = X/10 Cr; ₹ X billion (bn) = X*100 Cr;
  ₹ X lakh = X/100 Cr; ₹ X lakh crore = X*100000 Cr. USD: 1 USD = ₹{usd_inr}.
- order_book = unexecuted orders on hand at a date — never revenue, bids, pipeline, opportunities or orders
  received in a period. as_of = the date the quote states ("as on 12th August 2025" → 2025-08-12); for a
  quarter ("as of Q2 FY27") its end — FY27 Q1 ends 2026-06-30, Q2 2026-09-30, Q3 2026-12-31, Q4 2027-03-31;
  for "current", "today" or "as on date", the filing date. Use scope "total" for the company-wide figure.
- order_inflow = new orders received during a period.
- orders = individual NEW contracts or orders won (customer, value, duration as stated). Not reference lists
  of completed / commissioned projects, not customer logos, not bids, pipeline or opportunities.
- guidance = forward-looking targets management gives (growth %, margin %, revenue ₹ Cr, capex), with period.
  Percentages as plain numbers (20 for 20%). A single figure → low = high. scope = "total" only when the
  target is for the whole company; a segment, product line, geography or subsidiary target gets its name.
- tailwinds / headwinds / policy / interest_rate: at most 4 each, the most material.

DOCUMENT
{pages}"""


def _day() -> str:
    return now_ist().date().isoformat()


def usage(conn: sqlite3.Connection, day: str | None = None) -> dict:
    day = day or _day()
    r = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) pin, COALESCE(SUM(completion_tokens),0) pout, "
        "COALESCE(SUM(CASE WHEN purpose='backfill' THEN prompt_tokens + completion_tokens END),0) bf, "
        "SUM(ok=0) errors FROM llm_calls WHERE day=?", (day,)).fetchone()
    return {"day": day, "calls": r["calls"], "prompt_tokens": r["pin"], "completion_tokens": r["pout"],
            "tokens": r["pin"] + r["pout"], "backfill_tokens": r["bf"], "errors": r["errors"] or 0}


def cost_inr(prompt_tokens: int, completion_tokens: int, settings: dict) -> float:
    llm = settings["llm"]
    usd = prompt_tokens / 1e6 * llm["usd_per_m_input"] + completion_tokens / 1e6 * llm["usd_per_m_output"]
    return round(usd * settings["orders"]["usd_inr"], 2)


# ── verification ────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = (s or "").lower().replace("₹", " rs ").replace("’", "'").replace("–", "-")
    s = re.sub(r"(\w)-\s+(\w)", r"\1\2", s)          # hyphenated line breaks
    return re.sub(r"[^a-z0-9%.]+", " ", s).strip()


def _numbers(s: str) -> list[float]:
    out = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", s or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def number_in_quote(value: float | None, quote: str, usd_inr: float) -> bool:
    if value is None:
        return True
    factors = (1, 0.1, 100, 0.01, 1e5, 1e-7, usd_inr / 10, usd_inr * 100, usd_inr / 1e7, 10)
    for n in _numbers(quote):
        for f in factors:
            v = n * f
            if v and abs(v - value) / max(abs(value), 1e-9) < 0.025:
                return True
    return False


def verify(quote: str, page: int | None, pages: list[str]) -> tuple[float, int | None]:
    """(score, page) — exact normalised match = 1, ≥70% of 4-word shingles = 0.5, else 0."""
    q = _norm(quote)
    if len(q) < 8:
        return 0.0, page
    order = ([page] if page and 1 <= page <= len(pages) else []) + [i for i in range(1, len(pages) + 1) if i != page]
    normed = {}
    for p in order:
        normed[p] = _norm(pages[p - 1])
        if q in normed[p]:
            return 1.0, p
    words = q.split()
    if len(words) < 5:
        return 0.0, page
    shingles = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    best, best_p = 0.0, page
    for p in order:
        cov = sum(1 for sh in shingles if sh in normed[p]) / len(shingles)
        if cov > best:
            best, best_p = cov, p
    return (0.5, best_p) if best >= 0.7 else (0.0, page)


ORDER_TERM = re.compile(r"\border|contract|\bLo[AI]\b|letter of (award|intent|acceptance)|\baward|\bP\.?O\.?\b|"
                        r"work order|purchase order|\bbag(ged|s)?\b|secured|\bwon\b|\bwins?\b|empanel", re.I)
ORDER_BOOK_TERM = re.compile(r"order\s*-?\s*book|orderbook|backlog|un-?executed|orders? in hand|order position|"
                             r"outstanding orders?|executable orders?|pending orders?", re.I)
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_D1 = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})[,\s-]+(\d{4})")
_D2 = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")


def stated_date(quote: str) -> str | None:
    """A calendar date written in the quote ('12th August 2025', 'Feb 09, 2026'), if any."""
    for rx, (di, mi, yi) in ((_D1, (1, 2, 3)), (_D2, (2, 1, 3))):
        for m in rx.finditer(quote or ""):
            mon = _MON.get(m.group(mi)[:3].lower())
            if not mon:
                continue
            try:
                return date(int(m.group(yi)), mon, int(m.group(di))).isoformat()
            except ValueError:
                continue
    return None


def anchored(value: float | None, pages: list[str], page: int | None, usd_inr: float, term=ORDER_BOOK_TERM) -> int | None:
    """Page where the number and the term both appear (slides often scramble sentences)."""
    if value is None:
        return None
    order = ([page] if page and 1 <= page <= len(pages) else []) + [i for i in range(1, len(pages) + 1) if i != page]
    for p in order:
        if term.search(pages[p - 1]) and number_in_quote(value, pages[p - 1], usd_inr):
            return p
    return None


# ── one document ────────────────────────────────────────────────────────────

SEGMENT_HINT = re.compile(r"\b(segment|division|vertical|subsidiar|from (a|the|our) [\w-]+ business|"
                          r"(consumer|export|domestic|defen[cs]e|railway|retail|b2b|b2c|international) business)\b", re.I)


def guidance_scope(item: dict, quote: str) -> str:
    sc = str(item.get("scope") or "").strip()
    if sc and sc.lower() not in ("total", "company", "overall", "consolidated", "whole company"):
        return sc[:60]
    if not sc and SEGMENT_HINT.search(quote or ""):
        return "segment"          # older extractions had no scope: judge from the wording
    return "total"


def _fy_end(period: str | None) -> str | None:
    m = re.search(r"Q([1-4])\s*FY\s*'?(\d{2})", period or "", re.I)
    if not m:
        return None
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    return {1: f"{fy - 1}-06-30", 2: f"{fy - 1}-09-30", 3: f"{fy - 1}-12-31", 4: f"{fy}-03-31"}[q]


def build_prompt(conn: sqlite3.Connection, doc: sqlite3.Row, settings: dict) -> tuple[str, list[int], list[str]]:
    pages = page_texts(ROOT / doc["path"], doc["sha256"])
    hits = {int(k): v for k, v in json.loads(doc["hits"] or "{}").items()}
    keep = relevant_pages(pages, hits, settings["llm"]["max_input_tokens"])
    body = "\n\n".join(f"=== Page {p} ===\n{pages[p - 1].strip()}" for p in keep)
    s = conn.execute("SELECT name FROM stocks WHERE sid=?", (doc["sid"],)).fetchone()
    prompt = PROMPT.format(company=s["name"] if s else doc["sid"], sid=doc["sid"], kind=doc["kind"],
                           filed=doc["filed_at"][:10], schema=SCHEMA_HINT, usd_inr=settings["orders"]["usd_inr"],
                           pages=body)
    return prompt, keep, pages


PROMPT_VERSION = "v3"
RESPONSE_DIR = TRACKER_DIR / "llm"


def _cache_path(doc: sqlite3.Row, model: str):
    return RESPONSE_DIR / f"{doc['sha256']}_{model}_{PROMPT_VERSION}.json"


def call_llm(conn: sqlite3.Connection, doc: sqlite3.Row, prompt: str, settings: dict) -> dict:
    """One extraction call. The raw response is cached by PDF hash + model + prompt version, so a
    document is never paid for twice (retries and re-processing read the cache)."""
    from llm.client import get_client, get_model_name, is_gpt52

    cfg = settings["llm"]
    name = cfg["client"]
    model = get_model_name(name)
    cached = _cache_path(doc, model)
    if cached.exists():
        return json.loads(cached.read_text())
    client = get_client(name)
    kwargs = {"model": model, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": "You are a careful financial-document extraction engine. "
                                                         "Output strict JSON only."},
                           {"role": "user", "content": prompt}]}
    if is_gpt52(name):
        kwargs["max_completion_tokens"] = cfg["max_output_tokens"]
    else:
        kwargs["max_tokens"] = cfg["max_output_tokens"]
        kwargs["temperature"] = 0
    t0 = time.monotonic()
    stamp = now_ist().isoformat(timespec="seconds")
    purpose = "backfill" if doc["backfill"] else "filing"
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, "
                     "ok, error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (stamp, _day(), doc["id"], doc["sid"], model, purpose, len(prompt) // 4, 0,
                      int((time.monotonic() - t0) * 1000), 0, f"{exc.__class__.__name__}: {exc}"[:300]))
        raise
    u = resp.usage
    conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, ok, "
                 "error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (stamp, _day(), doc["id"], doc["sid"], model, purpose, u.prompt_tokens, u.completion_tokens,
                  int((time.monotonic() - t0) * 1000), 1, None))
    conn.execute("UPDATE documents SET tokens_in=?, tokens_out=?, model=? WHERE id=?",
                 (u.prompt_tokens, u.completion_tokens, model, doc["id"]))
    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
    RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _f(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _date(v) -> str | None:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat() if v else None
    except ValueError:
        return None


def persist(conn: sqlite3.Connection, doc: sqlite3.Row, data: dict, pages: list[str], settings: dict) -> dict:
    """Store what the model returned, with each quote checked against the PDF text."""
    usd = settings["orders"]["usd_inr"]
    sid, did = doc["sid"], doc["id"]
    url = doc["url"] or file_link(doc["path"])
    stamp = now_ist().isoformat(timespec="seconds")
    meta = data.get("document") or {}
    period = meta.get("period") if isinstance(meta, dict) and meta.get("period") else doc["period"]
    if period and period != doc["period"]:
        conn.execute("UPDATE documents SET period=? WHERE id=?", (str(period)[:40], did))
    filed = doc["filed_at"][:10]

    def check(item: dict, value=None) -> tuple[float, int | None, str]:
        quote = str(item.get("quote") or "")[:600]
        pg = _f(item.get("page"))
        score, page = verify(quote, int(pg) if pg else None, pages)
        if score and value is not None and not number_in_quote(value, quote, usd):
            score = min(score, 0.5)
        return score, page, quote

    counts = {k: 0 for k in ("order_book", "orders", "guidance", "facts")}
    for ob in data.get("order_book") or []:
        v = _f(ob.get("value_cr"))
        if not v or v <= 0:
            continue
        score, page, quote = check(ob, v)
        if score == 0:
            p2 = anchored(v, pages, page, usd)
            if p2:
                score, page = 0.5, p2
        if not ORDER_BOOK_TERM.search(quote):
            score = 0.0          # the quote is about bids, inflow or revenue — not an order book
        as_of = stated_date(quote) or _date(ob.get("as_of")) or _fy_end(period) or filed
        if re.search(r"as on date|as of date|as of today|currently|current order book|right now", quote, re.I) \
                and not stated_date(quote):
            as_of = filed
        if as_of > filed:
            as_of = filed
        cur = conn.execute(
            "INSERT OR IGNORE INTO order_book_history(sid, as_of, value_cr, scope, doc_id, method, quote, page, verified, "
            "url, path, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, as_of, round(v, 2), (ob.get("scope") or "total")[:60], did, "llm", quote, page, score, url, doc["path"],
             stamp))
        counts["order_book"] += cur.rowcount

    for o in data.get("orders") or []:
        v = _f(o.get("value_cr"))
        if v is None and doc["kind"] not in ("order_win",):
            continue             # reference lists of past projects carry no value — not new orders
        q_raw = str(o.get("quote") or "")
        if doc["kind"] != "order_win" and re.search(r"\b(completed|commissioned|delivered|opportunit|pipeline|bids?|"
                                                    r"targeting)\b", q_raw, re.I):
            continue             # a past project or a pipeline line, not a new order
        score, page, quote = check(o, v)
        if doc["kind"] != "order_win" and not ORDER_TERM.search(quote):
            # a row of an order table on a slide carries no "order" word itself — its page must
            on_order_page = doc["kind"] == "presentation" and page and 1 <= page <= len(pages) and \
                (ORDER_BOOK_TERM.search(pages[page - 1]) or re.search(r"\borders?\b", pages[page - 1], re.I))
            if not on_order_page:
                continue         # e.g. "business of ₹260 Cr this year" — revenue, not an order
        dur = _f(o.get("duration_months"))
        annual = round(v / (dur / 12), 2) if v and dur and dur > 12 else v
        own_date = _date(o.get("date")) or stated_date(quote)
        dated = 1 if (own_date or doc["kind"] == "order_win") else 0
        conn.execute(
            "INSERT INTO orders(sid, doc_id, event_id, announced_on, customer, order_type, value_cr, duration_months, "
            "annual_value_cr, description, quote, page, verified, url, method, dated, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, did, doc["event_id"], min(own_date or filed, filed), (o.get("customer") or None) and str(o["customer"])[:160],
             (o.get("order_type") or None) and str(o["order_type"])[:60], v, dur, annual,
             str(o.get("description") or "")[:400], quote, page, score, url, "llm", dated, stamp))
        counts["orders"] += 1

    for g in data.get("guidance") or []:
        lo, hi = _f(g.get("low")), _f(g.get("high"))
        score, page, quote = check(g, lo if lo is not None else hi)
        conn.execute(
            "INSERT INTO guidance(sid, doc_id, stated_on, metric, period, low, high, unit, text, quote, page, verified, url, "
            "scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, did, filed, str(g.get("metric") or "other")[:30], str(g.get("period") or "")[:20] or None, lo,
             hi if hi is not None else lo, str(g.get("unit") or "")[:10] or None, str(g.get("text") or "")[:300], quote,
             page, score, url, guidance_scope(g, quote)))
        counts["guidance"] += 1

    def fact(section: str, item: dict, metric=None, value=None, unit=None, per=None, text=None, extra=None):
        score, page, quote = check(item, value)
        conn.execute("INSERT INTO doc_facts(doc_id, sid, section, metric, period, value, unit, text, quote, page, verified, "
                     "data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (did, sid, section, metric, per, value, unit, (text or item.get("text") or "")[:400], quote, page,
                      score, json.dumps(extra) if extra else None))
        counts["facts"] += 1

    for x in data.get("order_inflow") or []:
        fact("order_inflow", x, value=_f(x.get("value_cr")), unit="cr", per=x.get("period"),
             text=f"Order inflow {x.get('period') or ''}".strip())
    ep = data.get("execution_period_months")
    if isinstance(ep, dict) and _f(ep.get("value")):
        fact("execution", ep, value=_f(ep.get("value")), unit="months", text="Order book execution period")
    for x in data.get("capex") or []:
        fact("capex", x, metric=str(x.get("what") or "")[:120], value=_f(x.get("amount_cr")), unit="cr",
             text=" · ".join(str(v) for v in (x.get("what"), x.get("capacity"), x.get("timeline")) if v),
             extra={k: x.get(k) for k in ("capacity", "timeline")})
    for section in ("tailwinds", "headwinds", "policy", "interest_rate"):
        for x in (data.get(section) or [])[:4]:
            fact(section.rstrip("s") if section != "interest_rate" else section, x)

    conn.execute("UPDATE documents SET status='extracted', error=NULL, updated_at=? WHERE id=?", (stamp, did))
    if not doc["backfill"]:
        _events(conn, doc, url)
    return counts


def _events(conn: sqlite3.Connection, doc: sqlite3.Row, url: str) -> None:
    """News from a freshly filed document: order book disclosed, guidance given."""
    sid = doc["sid"]
    ob = conn.execute("SELECT * FROM order_book_history WHERE doc_id=? AND scope='total' AND verified > 0 "
                      "ORDER BY value_cr DESC LIMIT 1", (doc["id"],)).fetchone()
    if ob:
        prev = conn.execute("SELECT value_cr, as_of FROM order_book_history WHERE sid=? AND scope='total' AND verified > 0 "
                            "AND as_of < ? ORDER BY as_of DESC LIMIT 1", (sid, ob["as_of"])).fetchone()
        chg = (ob["value_cr"] / prev["value_cr"] - 1) * 100 if prev and prev["value_cr"] else None
        title = f"Order book ₹{ob['value_cr']:,.0f} Cr as of {datetime.fromisoformat(ob['as_of']).strftime('%d %b %Y')}"
        if chg is not None:
            title += f" ({chg:+.1f}% vs {datetime.fromisoformat(prev['as_of']).strftime('%b %Y')})"
        add_event(conn, sid=sid, ts=doc["filed_at"], kind="filing", type="order_book",
                  severity="high" if chg is not None and abs(chg) >= 20 else "medium", title=title,
                  detail=ob["quote"], value_cr=ob["value_cr"], url=url, source=f"AI-extracted · {doc['kind']} p.{ob['page']}",
                  dedupe=f"doc:{doc['id']}:ob", data={"doc_id": doc["id"], "change_pct": chg, "page": ob["page"]})
    gs = conn.execute("SELECT * FROM guidance WHERE doc_id=? AND verified > 0", (doc["id"],)).fetchall()
    if gs:
        def fmt(g):
            rng = (f"{g['low']:g}" if g["low"] == g["high"] else f"{g['low']:g}–{g['high']:g}") if g["low"] is not None else ""
            unit = "%" if g["unit"] == "pct" else " Cr" if g["unit"] == "cr" else ""
            return f"{g['metric'].replace('_', ' ')} {g['period'] or ''} {rng}{unit}".strip()
        add_event(conn, sid=sid, ts=doc["filed_at"], kind="filing", type="guidance", severity="medium",
                  title="Guidance: " + "; ".join(fmt(g) for g in gs[:3]), detail=gs[0]["quote"], url=url,
                  source=f"AI-extracted · {doc['kind']}", dedupe=f"doc:{doc['id']}:guid",
                  data={"doc_id": doc["id"], "n": len(gs)})


def reprocess(conn: sqlite3.Connection, doc_id: int, settings: dict) -> dict | None:
    """Re-store a document from its cached response (after a fix to checks/storage) — no LLM call."""
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc or not doc["model"]:
        return None
    hits = [p for p in RESPONSE_DIR.glob(f"{doc['sha256']}_{doc['model']}_*.json")]
    if not hits:
        return None
    data = json.loads(sorted(hits)[-1].read_text())
    for t in ("order_book_history", "orders", "guidance", "doc_facts"):
        conn.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
    pages = page_texts(ROOT / doc["path"], doc["sha256"])
    return persist(conn, doc, data, pages, settings)


# ── queue ───────────────────────────────────────────────────────────────────

def run_queue(conn: sqlite3.Connection, settings: dict, deadline=lambda: True, limit: int = 20) -> dict:
    cfg = settings["llm"]
    out = {"done": 0, "deferred_budget": 0, "failed": 0, "tokens": 0}
    for doc in conn.execute("SELECT * FROM documents WHERE status='prepared' "
                            "ORDER BY backfill, priority DESC, filed_at DESC LIMIT ?", (limit,)).fetchall():
        if not deadline():
            break
        u = usage(conn)
        need = min(doc["est_tokens"] or 0, cfg["max_input_tokens"]) + 900 + cfg["max_output_tokens"]
        room = cfg["daily_token_budget"] - u["tokens"]
        if doc["backfill"]:
            room = min(room, cfg["daily_token_budget"] * cfg["backfill_share"] - u["backfill_tokens"])
        if need > room:
            out["deferred_budget"] += 1
            if doc["backfill"]:
                break           # everything after this is back-fill too
            continue
        try:
            prompt, keep, pages = build_prompt(conn, doc, settings)
            data = call_llm(conn, doc, prompt, settings)
            conn.commit()
            counts = persist(conn, doc, data if isinstance(data, dict) else {}, pages, settings)
            out["done"] += 1
            out.setdefault("found", {})
            for k, v in counts.items():
                out["found"][k] = out["found"].get(k, 0) + v
        except Exception as exc:
            out["failed"] += 1
            conn.execute("UPDATE documents SET error=?, status=CASE WHEN COALESCE(error,'') LIKE 'retry%' "
                         "THEN 'failed' ELSE status END, updated_at=? WHERE id=?",
                         (f"retry: {exc.__class__.__name__}: {exc}"[:300], now_ist().isoformat(timespec="seconds"),
                          doc["id"]))
        conn.commit()
    out["tokens"] = usage(conn)["tokens"]
    return out
