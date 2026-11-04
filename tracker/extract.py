"""
LLM extraction from one prepared document (documents.py), via the Azure OpenAI client in llm/client.py.

Per document, one call returns:
  • summary — 3–5 dense lines covering every critical point (shown on the filing's event)
  • order book, order inflow, orders, guidance (with numbers and scope), capex, tailwinds, headwinds,
    policy, interest-rate sensitivity — each with its verbatim quote and page
  • results (results, press releases, presentations): the quarter's numbers YoY / QoQ, anomalies, and how
    they compare with management's open commitments (given to the model as context)
  • quarter note + commitment checks (concall transcripts, presentations) → company_notes.py

Cost controls: only `prepared` documents (tracked stocks, keyword-relevant), only their relevant pages
trimmed to llm.max_input_tokens, one call per document (responses cached by PDF hash), a daily budget in
rupees (llm.daily_budget_inr) with back-fill capped at llm.backfill_share, every call logged in llm_calls.

Trust: quotes are checked against the page text — 1 = found verbatim (and the number is in it),
0.5 = near match or number not in the quote, 0 = not found. Unverified figures are kept but flagged.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from .config import ROOT, TRACKER_DIR
from .db import add_event
from .documents import file_link, page_texts, relevant_pages, settle_event
from . import numcheck
from .util import now_ist

BACKFILL_PURPOSES = ("backfill", "notes_backfill")
KIND_TITLE = {"results": "Results", "transcript": "Concall transcript", "presentation": "Investor presentation",
              "press_release": "Press release", "order_win": "Order win", "capacity": "Capacity / expansion",
              "acquisition": "Acquisition / restructuring", "meet": "Analyst / investor meet"}
CONTEXT_KINDS = ("results", "transcript", "presentation", "press_release")

BASE_SCHEMA = """  "document": {"type": "investor_presentation|earnings_call_transcript|results|order_win|press_release|other",
               "period": "quarter or year the document reports on, e.g. Q2 FY27", "consolidated": true|false|null},
  "summary": ["3-5 lines"],
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
  "interest_rate": [{"text": "sensitivity to interest rates / financing costs", "quote": "...", "page": n}]"""

RESULTS_SCHEMA = """  "results": {"period": "Q2 FY27", "basis": "consolidated|standalone",
              "revenue_cr": number|null, "revenue_yoy_pct": number|null, "revenue_qoq_pct": number|null,
              "ebitda_cr": number|null, "ebitda_margin_pct": number|null, "ebitda_margin_yoy_bp": number|null,
              "pat_cr": number|null, "pat_yoy_pct": number|null, "pat_qoq_pct": number|null, "eps": number|null,
              "pbt_cr": number|null, "finance_cost_cr": number|null, "depreciation_cr": number|null,
              "other_income_cr": number|null, "exceptional_cr": number|null,
              "prev_year": {"revenue_cr": number|null, "pbt_cr": number|null, "finance_cost_cr": number|null,
                            "depreciation_cr": number|null, "other_income_cr": number|null, "exceptional_cr": number|null} | null,
              "quote": "the sentence or table row with the revenue figure", "page": n,
              "anomalies": [{"text": "one-offs, exceptional items, other-income or tax swings, receivables / inventory jumps, auditor remarks", "quote": "...", "page": n}],
              "vs_commitments": [{"commitment": "from OPEN COMMITMENTS", "said_in": "Q1 FY27", "status": "ahead|on_track|behind|missed|unclear", "evidence": "one line with numbers"}],
              "verdict": "ahead|in_line|behind|mixed|no_commitments", "headline": "≤ 15 words"} | null"""

NOTE_SCHEMA = """  "quarter_note": {"period": "Q2 FY27",
                   "business": ["2-3 lines: what happened in the quarter, numbers first"],
                   "guidance": ["each guided number with metric, period and range"],
                   "commitments": [{"what": "...", "by_when": "Q4 FY27", "metric": "ebitda_margin|revenue|capacity|order_inflow|launch|other", "target": "..."}],
                   "triggers": [{"trigger": "what must happen for the story to play out", "expected_by": "..."}],
                   "tailwinds": ["name the policy or scheme"], "headwinds": ["..."], "policy": ["..."], "risks": ["..."],
                   "tone": "confident|steady|cautious|defensive"} | null,
  "commitment_checks": [{"commitment": "from OPEN COMMITMENTS", "said_in": "Q1 FY27", "status": "delivered|partly|missed|pending|changed",
                         "evidence": "one line with numbers", "quote": "...", "page": n}]"""

PROMPT = """You extract facts from an Indian listed company's exchange filing for an investor's tracker.
Company: {company} ({sid}). Document: {kind}, filed {filed}. Pages are marked "=== Page N ===".

Return ONE JSON object with exactly these keys (use [] or null when the document does not say):
{{
{schema}
}}

Rules:
- summary: 3-5 lines, each at most 25 words. Numbers first, no filler ("the company has informed", "we are pleased").
  Together the lines must cover every critical point: what was announced or reported, the key numbers with
  YoY / QoQ change, guidance with its numbers, orders, capex, anything unusual{summary_extra}.{summary_order}
  Never write what the document does not contain ("no order book disclosed") and never name the sections of
  this prompt (OPEN COMMITMENTS, CONTEXT) — write "management's earlier targets" instead.
- Only facts the document states. Never estimate. You may compute a YoY / QoQ % only from figures printed in
  the document.
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
- guidance = forward-looking targets with their numbers (growth %, margin %, revenue ₹ Cr, capex), with period.
  Percentages as plain numbers (20 for 20%). A single figure → low = high. If management gives a direction with
  no number, put it in the summary, not in guidance. scope = "total" only for the whole company; a segment,
  product line, geography or subsidiary target gets its name.
- tailwinds / headwinds / policy / interest_rate: at most 4 each, the most material.
- Tables are given one row per line, cells separated by " | ", in the column order of the header lines above
  them; an empty cell is a blank in the document. Read each figure from its own row and column.{extra_rules}
{context}
DOCUMENT
{pages}"""

RESULTS_RULES = """
- results: the quarter this document reports (prefer consolidated). Use the figures printed for the quarter and
  the comparable quarters. vs_commitments: check each OPEN COMMITMENT the quarter speaks to — copy its text exactly
  as given (the part before " — target"); "verdict" sums it up ("no_commitments" when none apply).
  pbt / finance cost / depreciation / other income / exceptional items: the quarter's figures as printed in the
  statement (exceptional_cr signed as it affects profit: a loss is negative), and the same for the year-ago quarter
  in prev_year. ebitda_cr and ebitda_margin_pct only when the document prints them — never derive them yourself.
  If the document has no figures for the quarter (only a cover letter or the auditor's review report, or the
  tables are scanned images), set results to null, say so in one summary line and judge no commitment."""
NOTE_RULES = """
- quarter_note: the condensed note an investor keeps for this quarter. commitments = what management now promises
  and by when; triggers = what must happen for the story to play out, with the expected timing.
- commitment_checks: one per OPEN COMMITMENT this document addresses — copy its text exactly as given (the part
  before " — target") and judge it against its target."""


def _day() -> str:
    return now_ist().date().isoformat()


# ── budget (₹ per IST day) ──────────────────────────────────────────────────

def cost_inr(prompt_tokens: float, completion_tokens: float, settings: dict) -> float:
    llm = settings["llm"]
    usd = prompt_tokens / 1e6 * llm["usd_per_m_input"] + completion_tokens / 1e6 * llm["usd_per_m_output"]
    return round(usd * settings["orders"]["usd_inr"], 2)


def budget(conn: sqlite3.Connection, settings: dict, day: str | None = None) -> dict:
    cfg = settings["llm"]
    cap = cfg.get("daily_budget_inr")
    if not cap:   # older settings: a token budget, priced as 85% input / 15% output
        t = cfg.get("daily_token_budget", 500000)
        cap = cost_inr(t * 0.85, t * 0.15, settings)
    spent = bf = 0.0
    by: dict[str, float] = {}
    for r in conn.execute("SELECT purpose, COALESCE(SUM(prompt_tokens),0) pin, COALESCE(SUM(completion_tokens),0) pout "
                          "FROM llm_calls WHERE day=? AND COALESCE(model, '') NOT LIKE 'local:%' GROUP BY purpose",
                          (day or _day(),)):      # the local model (localllm.py) costs nothing
        c = cost_inr(r["pin"], r["pout"], settings)
        by[r["purpose"]] = c
        spent += c
        if r["purpose"] in BACKFILL_PURPOSES:
            bf += c
    return {"cap_inr": float(cap), "spent_inr": round(spent, 2), "backfill_inr": round(bf, 2),
            "backfill_cap_inr": round(float(cap) * cfg["backfill_share"], 2), "by_purpose": by}


def can_spend(conn: sqlite3.Connection, settings: dict, est_in: float, est_out: float, purpose: str) -> bool:
    b = budget(conn, settings)
    need = cost_inr(est_in, est_out, settings)
    if b["spent_inr"] + need > b["cap_inr"]:
        return False
    if purpose in BACKFILL_PURPOSES and b["backfill_inr"] + need > b["backfill_cap_inr"]:
        return False
    return True


def usage(conn: sqlite3.Connection, day: str | None = None) -> dict:
    day = day or _day()
    r = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) pin, COALESCE(SUM(completion_tokens),0) pout, "
        f"COALESCE(SUM(CASE WHEN purpose IN ({','.join('?' * len(BACKFILL_PURPOSES))}) THEN prompt_tokens + completion_tokens END),0) bf, "
        "SUM(ok=0) errors FROM llm_calls WHERE day=?", (*BACKFILL_PURPOSES, day)).fetchone()
    return {"day": day, "calls": r["calls"], "prompt_tokens": r["pin"], "completion_tokens": r["pout"],
            "tokens": r["pin"] + r["pout"], "backfill_tokens": r["bf"], "errors": r["errors"] or 0}


class BudgetExceeded(Exception):
    pass


RESPONSE_DIR = TRACKER_DIR / "llm"
PROMPT_VERSION = "v6"          # v5: tables as rows (layout.py); v6: what a results summary covers, in order
SUMMARY_ORDER = """
  When the document has the period's financials, the lines go in this order: revenue with YoY / QoQ; expenses,
  EBITDA and margins (with the change in bps); net profit (PAT) and EPS, with YoY; the business's progress (orders,
  capacity, launches, guidance with numbers); the main tailwinds and headwinds. Say which period each figure is
  (Q2 FY27, H1 FY27) and whether it is consolidated or standalone."""


def llm_json(conn: sqlite3.Connection, settings: dict, prompt: str, *, purpose: str, sid: str | None,
             doc_id: int | None = None, cache_name: str | None = None, max_tokens: int | None = None,
             system: str = "You are a careful financial-document extraction engine. Output strict JSON only.",
             temperature: float = 0) -> dict:
    """One JSON-mode call, cached on disk under data/tracker/llm/<cache_name>.json (never paid twice)."""
    from llm.client import get_client, get_model_name, is_gpt52

    cfg = settings["llm"]
    name = cfg["client"]
    model = get_model_name(name)
    cached = RESPONSE_DIR / f"{cache_name}.json" if cache_name else None
    if cached and cached.exists():
        return json.loads(cached.read_text())
    out_tokens = max_tokens or cfg["max_output_tokens"]
    if not can_spend(conn, settings, len(prompt) / 4, out_tokens * 0.6, purpose):
        raise BudgetExceeded(f"daily LLM budget reached ({purpose})")
    # the SDK default (10 min × 3 tries) would hold a tick — and the documents lock — for half an hour
    client = get_client(name).with_options(timeout=float(cfg.get("request_timeout_s", 120)), max_retries=1)
    kwargs = {"model": model, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if is_gpt52(name):
        kwargs["max_completion_tokens"] = out_tokens
    else:
        kwargs["max_tokens"] = out_tokens
        kwargs["temperature"] = temperature
    t0 = time.monotonic()
    stamp = now_ist().isoformat(timespec="seconds")
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, "
                     "ok, error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (stamp, _day(), doc_id, sid, model, purpose, len(prompt) // 4, 0,
                      int((time.monotonic() - t0) * 1000), 0, f"{exc.__class__.__name__}: {exc}"[:300]))
        conn.commit()          # every caller is at a clean point here; a later rollback must not erase the spend
        raise
    u = resp.usage
    conn.execute("INSERT INTO llm_calls(ts, day, doc_id, sid, model, purpose, prompt_tokens, completion_tokens, ms, ok, "
                 "error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (stamp, _day(), doc_id, sid, model, purpose, u.prompt_tokens, u.completion_tokens,
                  int((time.monotonic() - t0) * 1000), 1, None))
    if doc_id:
        conn.execute("UPDATE documents SET tokens_in=?, tokens_out=?, model=? WHERE id=?",
                     (u.prompt_tokens, u.completion_tokens, model, doc_id))
    conn.commit()              # the tokens are paid: keep the record whatever happens to the response
    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        data = json_repair(m.group(0)) if m else {}
    if cached:
        RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, ensure_ascii=False))
    return data


# ── verification ────────────────────────────────────────────────────────────

def json_repair(text: str):
    """Parse JSON the model almost got right: a missing comma between members, or a trailing comma."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r'([\]}"0-9]|true|false|null)(\s*\n\s*)(?=["{\[])', r"\1,\2", text)   # missing commas at line breaks
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)                                      # trailing commas
    return json.loads(fixed)


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
    from .results import period_end
    return period_end(period)


def _wants(doc: sqlite3.Row) -> tuple[bool, bool]:
    """(results section, quarter-note section) for this document — live filings only."""
    if doc["backfill"]:
        return False, False
    return doc["kind"] in ("results", "press_release", "presentation"), doc["kind"] in ("transcript", "presentation")


def build_prompt(conn: sqlite3.Connection, doc: sqlite3.Row, settings: dict) -> tuple[str, list[int], list[str]]:
    from .company_notes import context_block

    pages = page_texts(ROOT / doc["path"], doc["sha256"])
    hits = {int(k): v for k, v in json.loads(doc["hits"] or "{}").items()}
    from .documents import reads_financials
    keep = relevant_pages(pages, hits, settings["llm"]["max_input_tokens"],
                          include_financials=reads_financials(doc["kind"]) and not doc["backfill"])
    if doc["kind"] in ("results", "order_win", "press_release") and len(pages) <= 12:
        keep = list(range(1, len(pages) + 1))      # short filings: send all of it
    if not keep:
        keep = list(range(1, min(len(pages), 12) + 1))
    body = "\n\n".join(f"=== Page {p} ===\n{pages[p - 1].strip()}" for p in keep)
    s = conn.execute("SELECT name FROM stocks WHERE sid=?", (doc["sid"],)).fetchone()
    want_results, want_note = _wants(doc)
    schema = BASE_SCHEMA + (",\n" + RESULTS_SCHEMA if want_results else "") + (",\n" + NOTE_SCHEMA if want_note else "")
    ctx = context_block(conn, doc["sid"]) if (want_results or want_note) else ""
    prompt = PROMPT.format(
        company=s["name"] if s else doc["sid"], sid=doc["sid"], kind=doc["kind"], filed=doc["filed_at"][:10],
        schema=schema, usd_inr=settings["orders"]["usd_inr"], pages=body,
        summary_extra=(", and — only when the document has the quarter's figures — whether the quarter is ahead of, "
                       "in line with or behind what management promised earlier"
                       if want_results or want_note else ""),
        summary_order=SUMMARY_ORDER if want_results or doc["kind"] == "transcript" else "",
        extra_rules=(RESULTS_RULES if want_results else "") + (NOTE_RULES if want_note else ""),
        context=f"\nCONTEXT FROM EARLIER QUARTERS\n{ctx}\n" if ctx else "")
    return prompt, keep, pages


def call_llm(conn: sqlite3.Connection, doc: sqlite3.Row, prompt: str, settings: dict) -> dict:
    """The extraction call for one document, cached by PDF hash + model + prompt version. Live filings
    carry earlier-quarter context, so their cache key also includes a short hash of the prompt."""
    import hashlib

    from llm.client import get_model_name

    model = get_model_name(settings["llm"]["client"])
    ph = hashlib.sha256(prompt.encode()).hexdigest()[:10]
    old = RESPONSE_DIR / f"{doc['sha256']}_{model}_v3.json"          # earlier prompt versions stay usable
    if doc["backfill"] and old.exists():
        return json.loads(old.read_text())
    name = f"{doc['sha256']}_{model}_{PROMPT_VERSION}" + ("" if doc["backfill"] else f"_{ph}")
    return llm_json(conn, settings, prompt, purpose="backfill" if doc["backfill"] else "filing", sid=doc["sid"],
                    doc_id=doc["id"], cache_name=name)


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


FILLER = re.compile(r"\bnot (?:disclosed|provided|reported|given|stated|available|mentioned)\b|"
                    r"^\s*(there (is|are|were) )?no\b[^.;]*\b(disclosed|provided|announced|mentioned|given|reported|"
                    r"stated|available)\b|\bnull\b|\b(results|this) section\b|OPEN COMMITMENTS|cannot be (judged|assessed)"
                    r"|^\s*(none|nil|n/?a)\W*$|^\s*no (exceptional|unusual|one-?off|anomal)", re.I)


DROPPED_NOTE = "A line was left out: its figure isn't in the filing — check the PDF"

NO_TARGETS = re.compile(r"\b(earlier|previous|prior) (targets|guidance|commitments)\b|"
                        r"\b(management'?s|ahead of|behind|in line with) (targets|guidance|commitments)\b", re.I)


def drop_filler(text: str) -> str:
    """Drop the clauses that only say what a filing lacks; keep any clause with a figure."""
    return "; ".join(c.strip(" .") for c in re.split(r";\s*|\.\s+(?=[A-Z])", str(text or ""))
                     if c.strip(" .") and not (FILLER.search(c) and not re.search(r"\d", c)))


def _summary(data: dict) -> list[str]:
    s = data.get("summary")
    if isinstance(s, str):
        s = [x.strip(" -•") for x in re.split(r"\n+", s) if x.strip()]
    lines = [str(x).strip()[:240] for x in (s or []) if str(x).strip()]
    kept = [y for y in (drop_filler(x) for x in lines) if y]   # "no order book disclosed" etc. say nothing
    return (kept or lines[:1])[:5]


def persist(conn: sqlite3.Connection, doc: sqlite3.Row, data: dict, pages: list[str], settings: dict) -> dict:
    """Store what the model returned, with each quote checked against the PDF text."""
    from . import company_notes

    usd = settings["orders"]["usd_inr"]
    sid, did = doc["sid"], doc["id"]
    url = doc["url"] or file_link(doc["path"])
    stamp = now_ist().isoformat(timespec="seconds")
    meta = data.get("document") or {}
    period = meta.get("period") if isinstance(meta, dict) and meta.get("period") else doc["period"]
    if period and period != doc["period"]:
        conn.execute("UPDATE documents SET period=? WHERE id=?", (str(period)[:40], did))
    filed = doc["filed_at"][:10]
    summary = _summary(data)
    # every figure the AI wrote, checked against the tables and text of the filing (numcheck.py) before it is stored
    want_res, _ = _wants(doc)
    res_in = data.get("results") if want_res and isinstance(data.get("results"), dict) else None
    want_note_ = _wants(doc)[1]
    # what the prompt carried, as evidence only for a concall: a results filing's own figures must come from the
    # filing (the context's latest quarter can hold an earlier, unchecked read of this very filing — DANISH H1 FY27)
    ctx = company_notes.context_block(conn, sid) if doc["kind"] == "transcript" else ""
    name = conn.execute("SELECT name FROM stocks WHERE sid=?", (sid,)).fetchone()
    chk = numcheck.check(summary, res_in, pages, period=str(period or ""), conn=conn, sid=sid, usd_inr=usd,
                         context=ctx, company=name["name"] if name else "")
    summary = chk.summary or summary[:0]
    if chk.dropped:
        summary.append(DROPPED_NOTE if len(chk.dropped) == 1 else DROPPED_NOTE.replace("A line", f"{len(chk.dropped)} lines"))
    if res_in is not None:
        data["results"] = chk.results
    conn.execute("UPDATE documents SET summary=?, numcheck=? WHERE id=?",
                 (json.dumps(summary, ensure_ascii=False) if summary else None,
                  json.dumps({"source": chk.source, "fixes": chk.fixes, "dropped": chk.dropped}, ensure_ascii=False), did))

    def check(item: dict, value=None) -> tuple[float, int | None, str]:
        quote = str(item.get("quote") or "")[:600]
        pg = _f(item.get("page"))
        score, page = verify(quote, int(pg) if pg else None, pages)
        if score and value is not None and not number_in_quote(value, quote, usd):
            score = min(score, 0.5)
        return score, page, quote

    counts = {k: 0 for k in ("order_book", "orders", "guidance", "facts", "notes", "checks")}
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

    want_results, want_note = _wants(doc)
    res = data.get("results") if want_results and isinstance(data.get("results"), dict) else None
    if want_note:
        note = data.get("quarter_note")
        if isinstance(note, dict):
            note.setdefault("period", period)
            src = "concall" if doc["kind"] == "transcript" else "presentation"
            counts["notes"] += company_notes.store_note(conn, sid, note, source=src, doc_id=did, url=url)
        counts["checks"] += company_notes.store_checks(
            conn, sid, data.get("commitment_checks") or [], checked_in=period, doc_id=did, url=url,
            verify=lambda q, p: verify(q, int(_f(p)) if _f(p) else None, pages))

    conn.execute("UPDATE documents SET status='extracted', error=NULL, updated_at=? WHERE id=?", (stamp, did))
    if not doc["backfill"]:
        _events(conn, doc, url, summary, res, period, pages, settings)
        counts["needs_view"] = bool(counts["notes"] or res)   # refreshed by the caller after committing
    return counts


def refresh_view_after(conn: sqlite3.Connection, sid: str, settings: dict) -> None:
    """The company view is a separate LLM call — run it only after the document's writes are committed,
    so the SQLite write lock is never held across a network call."""
    from . import company_notes
    try:
        company_notes.refresh_view(conn, sid, settings)
        conn.commit()
    except Exception as exc:  # budget or a failed call: the view refreshes next time
        conn.rollback()
        print(f"view refresh {sid}: {exc.__class__.__name__}: {exc}")


# ── events ──────────────────────────────────────────────────────────────────

def _recent(conn: sqlite3.Connection, sid: str, type_: str, ts: str, days: int = 3) -> list[sqlite3.Row]:
    since = (datetime.fromisoformat(ts) - timedelta(days=days)).isoformat()
    return conn.execute("SELECT * FROM events WHERE sid=? AND type=? AND ts >= ?", (sid, type_, since)).fetchall()


def _gfmt(g) -> str:
    metric = (g["metric"] or "other").replace("_", " ")
    if metric == "other" and g["text"]:
        metric = g["text"][:40]
    lo, hi = g["low"], g["high"]
    rng = f"{lo:g}" if lo is not None and (hi is None or hi == lo) else f"{lo:g}–{hi:g}" if lo is not None else f"{hi:g}"
    unit = g["unit"] or ""
    num = f"₹{rng} Cr" if unit == "cr" else f"{rng}%" if unit == "pct" else f"{rng}×" if unit == "x" else f"{rng} {unit}".strip()
    scope = f" ({g['scope']})" if g["scope"] and g["scope"] != "total" else ""
    return f"{g['period'] or ''} {metric}{scope} {num}".strip()


def _events(conn: sqlite3.Connection, doc: sqlite3.Row, url: str, summary: list[str], res: dict | None, period: str | None,
            pages: list[str], settings: dict) -> None:
    """Turn a freshly read filing into what the inbox shows: one informative event per thing that happened."""
    from . import company_notes, results

    sid = doc["sid"]
    kind = doc["kind"]
    ts = doc["filed_at"]
    per = results.norm_period(period) or period or ""
    src_event = doc["event_id"]
    src = conn.execute("SELECT * FROM events WHERE id=?", (src_event,)).fetchone() if src_event else None
    subject = (json.loads(src["data"] or "{}") or {}).get("subject") if src else None
    revised = bool(results.REVISION.search(" ".join(x for x in (doc["title"], subject) if x)))
    text = "\n".join(summary) if summary else None

    # the same kind of document for the same quarter, already read in the last week → no second alert
    twin = None
    since7 = (datetime.fromisoformat(ts) - timedelta(days=7)).isoformat()
    mine = {round(o["value_cr"], 1) for o in conn.execute("SELECT value_cr FROM orders WHERE doc_id=? AND value_cr > 0",
                                                         (doc["id"],))}
    for r in conn.execute("SELECT id, period FROM documents WHERE sid=? AND kind=? AND id != ? AND status='extracted' "
                          "AND backfill=0 AND filed_at >= ?", (sid, kind, doc["id"], since7)).fetchall():
        if per and (results.norm_period(r["period"]) or r["period"]) == per:
            if kind == "order_win" and mine and not mine <= {round(o["value_cr"], 1) for o in conn.execute(
                    "SELECT value_cr FROM orders WHERE doc_id=? AND value_cr > 0", (r["id"],))}:
                continue         # another order the same week (ADVAIT's two DGVCL orders), not the same one filed again
            twin = r
            break

    if res and (res.get("revenue_cr") is not None or res.get("pat_cr") is not None):
        usd = settings["orders"]["usd_inr"]
        rev = _f(res.get("revenue_cr"))
        quote = str(res.get("quote") or "")[:600]
        pg = _f(res.get("page"))
        score, page = verify(quote, int(pg) if pg else None, pages)       # checked like every other figure
        if score and rev is not None and not number_in_quote(rev, quote, usd):
            score = min(score, 0.5)
        n = {k: _f(res.get(k)) for k in ("revenue_cr", "revenue_yoy_pct", "revenue_qoq_pct", "ebitda_cr", "ebitda_margin_pct",
                                          "ebitda_margin_yoy_bp", "pat_cr", "pat_yoy_pct", "pat_qoq_pct", "eps")}
        n.update(results.pdf_ebitda(res, n))
        n.update(source="pdf", basis=res.get("basis"), verified=max(score, _f(res.get("verified")) or 0), url=url, page=page,
                 checked=res.get("numbers_check"))
        checks, verdict = company_notes.real_checks(conn, sid, res.get("vs_commitments") or [], res.get("verdict"))
        if not checks:   # nothing management promised applies: a "behind earlier targets" line would be invented
            summary = [x for x in summary if not NO_TARGETS.search(x)] or summary[:1]
            text = "\n".join(summary) if summary else None
            conn.execute("UPDATE documents SET summary=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), doc["id"]))
        anomalies = []
        for a in res.get("anomalies") or []:
            t = a.get("text") if isinstance(a, dict) else str(a)
            t = drop_filler(t)
            if t:
                anomalies.append({**a, "text": t} if isinstance(a, dict) else t)
        review = {"summary": summary, "anomalies": anomalies, "vs_commitments": checks,
                  "checked": {"table": 2, "xbrl": 1}.get(res.get("numbers_check") or "", 0),
                  "verdict": verdict, "headline": res.get("headline")}
        rper = results.norm_period(res.get("period")) or per
        prev = conn.execute("SELECT doc_id FROM results_reviews WHERE sid=? AND period=?", (sid, rper)).fetchone()
        results.upsert(conn, sid, rper, numbers=n, review=review, doc_id=doc["id"], url=url, ts=ts,
                       replace=bool(prev and prev["doc_id"] == doc["id"]), revised=revised)
        if src:   # the results event carries it; this filing stays in the timeline with its summary
            sev = "medium" if revised else ("medium" if kind == "presentation" and not twin else "low")
            title = f"Results revised / corrected · {per}" if revised else f"{KIND_TITLE.get(kind, src['title'])} · {per}".strip(" ·")
            settle_event(conn, src_event, severity=sev, title=title, detail=text)
    elif src:
        sev = None if not twin else "low"
        if kind in ("transcript", "presentation") and not twin:
            sev = "medium"
        label = KIND_TITLE.get(kind, src["title"])
        if kind == "order_win" and src["value_cr"]:
            label = src["title"]
        top = conn.execute("SELECT value_cr, customer FROM orders WHERE doc_id=? AND value_cr > 0 ORDER BY value_cr DESC LIMIT 1",
                           (doc["id"],)).fetchone() if kind == "order_win" else None
        if top:                  # the read found the order: its value and customer in the title, and on the event
            who = re.sub(r"\s*\(.*?\)\s*$", "", top["customer"] or "").strip()
            label = f"Order win ₹{top['value_cr']:,.1f} Cr" + (f" · {who[:40]}" if who else "")
            conn.execute("UPDATE events SET value_cr=? WHERE id=? AND value_cr IS NULL", (round(top["value_cr"], 2), src_event))
            if not twin:         # the importance the RSS item couldn't judge without the value (classify.py's rule)
                st = conn.execute("SELECT sales_ttm_cr FROM stocks WHERE sid=?", (sid,)).fetchone()
                big = top["value_cr"] >= 100 or (st and st["sales_ttm_cr"] and
                                                 top["value_cr"] >= settings["orders"]["high_pct_of_revenue"] / 100 * st["sales_ttm_cr"])
                sev = "high" if big else "medium"
        title = f"{label} · {per}".strip(" ·") if kind in ("transcript", "presentation", "results") else \
            label if top else None
        if twin:
            title = f"{label} · {per} (filed again)".strip(" ·")
        settle_event(conn, src_event, severity=sev, title=title, detail=text)

    ob = conn.execute("SELECT * FROM order_book_history WHERE doc_id=? AND scope='total' AND verified > 0 "
                      "ORDER BY value_cr DESC LIMIT 1", (doc["id"],)).fetchone()
    if ob and not any(abs((e["value_cr"] or 0) - ob["value_cr"]) <= 0.01 * ob["value_cr"]
                      for e in _recent(conn, sid, "order_book", ts)):
        prev = conn.execute("SELECT value_cr, as_of FROM order_book_history WHERE sid=? AND scope='total' AND verified > 0 "
                            "AND as_of < ? ORDER BY as_of DESC LIMIT 1", (sid, ob["as_of"])).fetchone()
        chg = (ob["value_cr"] / prev["value_cr"] - 1) * 100 if prev and prev["value_cr"] else None
        title = f"Order book ₹{ob['value_cr']:,.0f} Cr as of {datetime.fromisoformat(ob['as_of']).strftime('%d %b %Y')}"
        if chg is not None:
            title += f" ({chg:+.1f}% vs {datetime.fromisoformat(prev['as_of']).strftime('%b %Y')})"
        add_event(conn, sid=sid, ts=ts, kind="filing", type="order_book",
                  severity="high" if chg is not None and abs(chg) >= 20 else "medium", title=title,
                  detail=ob["quote"], value_cr=ob["value_cr"], url=url, source=f"AI-extracted · {doc['kind']} p.{ob['page']}",
                  dedupe=f"doc:{doc['id']}:ob", data={"doc_id": doc["id"], "change_pct": chg, "page": ob["page"]})

    gs = [g for g in conn.execute("SELECT * FROM guidance WHERE doc_id=? AND verified > 0", (doc["id"],)).fetchall()
          if g["low"] is not None or g["high"] is not None]
    if gs:
        items = [_gfmt(g) for g in gs]
        title = "Guidance: " + "; ".join(items[:4]) + (f" (+{len(items) - 4} more)" if len(items) > 4 else "")
        if not any(e["title"] == title for e in _recent(conn, sid, "guidance", ts)):
            detail = "\n".join(f"{_gfmt(g)} — “{(g['quote'] or '')[:180]}”" for g in gs[:6])
            add_event(conn, sid=sid, ts=ts, kind="filing", type="guidance", severity="medium", title=title, detail=detail,
                      url=url, source=f"AI-extracted · {doc['kind']}", dedupe=f"doc:{doc['id']}:guid",
                      data={"doc_id": doc["id"], "n": len(gs)})


# ── second read by the local model (a week of side-by-side reads, then the user picks) ───────────────

# the worked example the local model gets before each filing: what a results summary covers, in order (one-shot)
ONE_SHOT_USER = """(example) Company: Example Industries Ltd. Document: results. Pages are marked "=== Page N ===".
=== Page 1 ===
Statement of unaudited consolidated financial results for the quarter ended 30/09/2026 (₹ in crore)
Particulars | 30/09/2026 | 30/06/2026 | 30/09/2025 | 31/03/2026
Revenue from operations | 412.50 | 380.10 | 350.20 | 1,402.00
Other income | 6.20 | 5.10 | 4.00 | 18.30
Total expenses | 352.40 | 330.80 | 309.10 | 1,231.40
Finance costs | 8.10 | 8.30 | 9.00 | 34.60
Depreciation and amortisation | 12.40 | 12.10 | 11.20 | 46.00
Profit before tax | 66.30 | 54.40 | 45.10 | 189.00
Net profit for the period | 49.60 | 40.70 | 33.80 | 141.20
Basic EPS (₹) | 4.96 | 4.07 | 3.38 | 14.12
Order book as on 30 September 2026 stood at ₹2,150 crore; new orders of ₹520 crore were received in the quarter.
Management said demand from data-centre customers stayed strong, while higher copper prices weighed on gross margin."""
ONE_SHOT_ANSWER = json.dumps({"summary": [
    "Q2 FY27 consolidated revenue ₹412.5 Cr, up 17.8% YoY and 8.5% QoQ",
    "Total expenses ₹352.4 Cr, up 14.0% YoY; EBITDA ₹80.6 Cr, margin 19.5%, up 200 bps YoY",
    "Consolidated PAT ₹49.6 Cr, up 46.7% YoY and 21.9% QoQ; EPS ₹4.96 vs ₹3.38 a year ago",
    "Order book ₹2,150 Cr (5.2x quarterly revenue) as of 30 Sep 2026; Q2 order inflow ₹520 Cr",
    "Tailwind: strong data-centre demand; headwind: higher copper prices weighed on gross margin"]}, ensure_ascii=False)


def shadow_due(conn: sqlite3.Connection, settings: dict) -> sqlite3.Row | None:
    """The newest live filing read by gpt-4.1 that the local model hasn't read yet, inside the comparison week."""
    cfg = settings["llm"]
    until, since = cfg.get("shadow_until"), cfg.get("shadow_from") or "2000-01-01"
    if not until or _day() > until:
        return None
    return conn.execute("SELECT d.* FROM documents d WHERE d.backfill=0 AND d.status='extracted' AND d.filed_at >= ? "
                        "AND NOT EXISTS (SELECT 1 FROM shadow_reads s WHERE s.doc_id=d.id) "
                        "ORDER BY d.filed_at DESC LIMIT 1", (since,)).fetchone()


def shadow_read(conn: sqlite3.Connection, doc: sqlite3.Row, settings: dict) -> dict:
    """The local model reads the filing with the same pages and prompt (plus the worked example) and the same number
    check; its lines go on the filing's event and its results card beside gpt-4.1's. Nothing else is stored from it."""
    import hashlib

    from . import localllm, results
    from .company_notes import context_block

    t0 = time.monotonic()
    model = settings["llm"]["local_model"]
    prompt, keep, pages = build_prompt(conn, doc, settings)
    name = f"{doc['sha256']}_local-{model.replace(':', '-')}-1shot_{PROMPT_VERSION}_{hashlib.sha256(prompt.encode()).hexdigest()[:10]}"
    data = localllm.chat_json(conn, settings, prompt, purpose="shadow", sid=doc["sid"], cache_name=name,
                              examples=[(ONE_SHOT_USER, ONE_SHOT_ANSWER)])
    want_res, _ = _wants(doc)
    res = data.get("results") if want_res and isinstance(data.get("results"), dict) else None
    stock = conn.execute("SELECT name FROM stocks WHERE sid=?", (doc["sid"],)).fetchone()
    period = (data.get("document") or {}).get("period") or doc["period"]
    chk = numcheck.check(_summary(data), res, pages, period=str(period or ""), conn=conn, sid=doc["sid"],
                         usd_inr=settings["orders"]["usd_inr"],
                         context=context_block(conn, doc["sid"]) if doc["kind"] == "transcript" else "",
                         company=stock["name"] if stock else "")
    lines = list(chk.summary) + ([DROPPED_NOTE] if chk.dropped else [])
    secs = round(time.monotonic() - t0, 1)
    conn.execute("INSERT OR REPLACE INTO shadow_reads(doc_id, model, summary, numcheck, secs, created_at) VALUES(?,?,?,?,?,?)",
                 (doc["id"], localllm.LOCAL_PREFIX + model, json.dumps(lines, ensure_ascii=False),
                  json.dumps({"source": chk.source, "fixes": chk.fixes, "dropped": chk.dropped}, ensure_ascii=False), secs,
                  now_ist().isoformat(timespec="seconds")))
    events = {doc["event_id"]} if doc["event_id"] else set()
    rr = conn.execute("SELECT event_id FROM results_reviews WHERE sid=? AND doc_id=?", (doc["sid"], doc["id"])).fetchone() \
        or conn.execute("SELECT event_id FROM results_reviews WHERE sid=? AND period=?",
                        (doc["sid"], results.norm_period(period) or period)).fetchone()
    if rr and rr["event_id"]:
        events.add(rr["event_id"])
    for eid in events:
        row = conn.execute("SELECT data FROM events WHERE id=?", (eid,)).fetchone()
        if row is None:
            continue
        d = json.loads(row["data"] or "{}") if row["data"] else {}
        reads = d.get("local_reads") or {}
        reads[str(doc["id"])] = {"kind": doc["kind"], "lines": lines, "model": model}
        d["local_reads"] = reads
        conn.execute("UPDATE events SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False, default=str), eid))
    conn.commit()
    return {"doc_id": doc["id"], "sid": doc["sid"], "secs": secs, "lines": len(lines)}


def reread(conn: sqlite3.Connection, doc_id: int, settings: dict) -> dict | None:
    """Read a document again with the current pipeline (page text, prompt, number check) — one LLM call unless the
    same prompt was read before — and replace what its earlier read stored; its results event is rewritten."""
    from .documents import page_modes, prepare

    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc or not doc["path"]:
        return None
    conn.execute("UPDATE documents SET status='queued', updated_at=? WHERE id=?",
                 (now_ist().isoformat(timespec="seconds"), doc_id))
    status = prepare(conn, conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if status != "prepared":
        conn.commit()
        return {"status": status}
    prompt, keep, pages = build_prompt(conn, doc, settings)
    data = call_llm(conn, doc, prompt, settings)
    conn.commit()
    for t in ("order_book_history", "orders", "guidance", "doc_facts", "commitment_checks"):
        conn.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
    counts = persist(conn, doc, data if isinstance(data, dict) else {}, pages, settings)
    conn.commit()
    if counts.get("needs_view"):
        refresh_view_after(conn, doc["sid"], settings)
    row = conn.execute("SELECT summary, numcheck FROM documents WHERE id=?", (doc_id,)).fetchone()
    return {"status": "extracted", "pages_sent": keep, "page_modes": "".join(m[0] for m in page_modes(doc["sha256"])),
            "summary": json.loads(row["summary"] or "[]"), "numcheck": json.loads(row["numcheck"] or "{}")}


def reprocess(conn: sqlite3.Connection, doc_id: int, settings: dict) -> dict | None:
    """Re-store a document from its cached response (after a fix to checks/storage) — no LLM call."""
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc or not doc["model"]:
        return None
    hits = sorted(RESPONSE_DIR.glob(f"{doc['sha256']}_{doc['model']}_*.json"), key=lambda p: p.stat().st_mtime)
    if not hits:
        return None
    data = json.loads(hits[-1].read_text())
    for t in ("order_book_history", "orders", "guidance", "doc_facts", "commitment_checks"):
        conn.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
    pages = page_texts(ROOT / doc["path"], doc["sha256"])
    counts = persist(conn, doc, data, pages, settings)
    conn.commit()
    if counts.get("needs_view"):
        refresh_view_after(conn, doc["sid"], settings)
    return counts


# ── queue ───────────────────────────────────────────────────────────────────

def run_queue(conn: sqlite3.Connection, settings: dict, deadline=lambda: True, limit: int = 20,
              which: str | None = None) -> dict:
    """Extract prepared documents: new filings first. `which` = 'live' | 'backfill' limits to one kind."""
    cfg = settings["llm"]
    out = {"done": 0, "deferred_budget": 0, "failed": 0}
    only = {"live": "AND backfill=0", "backfill": "AND backfill=1"}.get(which or "", "")
    for doc in conn.execute(f"SELECT * FROM documents WHERE status='prepared' {only} "
                            "ORDER BY backfill, priority DESC, filed_at DESC LIMIT ?", (limit,)).fetchall():
        if not deadline():
            break
        est_in = min(doc["est_tokens"] or 0, cfg["max_input_tokens"]) + (2500 if not doc["backfill"] else 900)
        if not can_spend(conn, settings, est_in, cfg["max_output_tokens"] * 0.6,
                         "backfill" if doc["backfill"] else "filing"):
            out["deferred_budget"] += 1
            if doc["backfill"]:
                break           # everything after this is back-fill too
            settle_event(conn, doc["event_id"])   # the day's cap is spent: show the filing as it came (read tomorrow)
            conn.commit()
            continue
        counts = None
        try:
            prompt, keep, pages = build_prompt(conn, doc, settings)
            data = call_llm(conn, doc, prompt, settings)
            conn.commit()                         # the spend is recorded; the response is cached on disk
            conn.execute("SAVEPOINT persist_doc")
            try:
                counts = persist(conn, doc, data if isinstance(data, dict) else {}, pages, settings)
                conn.execute("RELEASE persist_doc")
            except Exception:
                conn.execute("ROLLBACK TO persist_doc")    # all or nothing: no half-stored document
                conn.execute("RELEASE persist_doc")
                raise
            out["done"] += 1
            out.setdefault("found", {})
            for k, v in counts.items():
                if k != "needs_view":
                    out["found"][k] = out["found"].get(k, 0) + v
        except BudgetExceeded:
            out["deferred_budget"] += 1
            if doc["backfill"]:
                break
            settle_event(conn, doc["event_id"])
        except Exception as exc:
            out["failed"] += 1
            conn.execute("UPDATE documents SET error=?, status=CASE WHEN COALESCE(error,'') LIKE 'retry%' "
                         "THEN 'failed' ELSE status END, updated_at=? WHERE id=?",
                         (f"retry: {exc.__class__.__name__}: {exc}"[:300], now_ist().isoformat(timespec="seconds"),
                          doc["id"]))
            if conn.execute("SELECT status FROM documents WHERE id=?", (doc["id"],)).fetchone()["status"] == "failed":
                settle_event(conn, doc["event_id"])      # give up on the AI read: show the filing as it came
        conn.commit()
        if counts and counts.get("needs_view"):
            refresh_view_after(conn, doc["sid"], settings)
    b = budget(conn, settings)
    out["spent_inr"] = b["spent_inr"]
    return out
