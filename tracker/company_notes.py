"""
Company notes — a condensed, quarter-by-quarter record of each company's story.

  • quarterly notes: one per company per quarter. New ones come from each concall transcript or
    investor presentation, written in the same LLM call that extracts the document (extract.py).
    Past ones (up to four quarters) are condensed once from the reports the existing workflows
    produced: transcript_analysis, master_report, thesis_review.
  • commitments: what management promised and by when. Each new transcript or results document is
    checked against the open commitments (delivered / partly / missed / pending / changed).
  • view: what the company does, its sector drivers and policies, the story and when it should play
    out, triggers, what to watch, delivery record and trend over the last four quarters. Refreshed after
    every new note or results review — a small LLM call over the notes, never over PDFs.

Everything spends from the same daily LLM budget (extract.budget); back-fill is capped by
llm.backfill_share so new filings always have room.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .config import COMPANIES_DIR, ROOT
from .results import norm_period, period_end
from .util import now_ist

NOTE_SCHEMA = """{
  "period": "Q2 FY27",
  "business": ["2-3 lines: what happened in the quarter, numbers first"],
  "guidance": ["each guided number with metric, period and range, e.g. 'FY27 revenue growth 20-25%'"],
  "commitments": [{"what": "EBITDA margin back to 18%", "by_when": "Q4 FY27", "metric": "ebitda_margin|revenue|capacity|order_inflow|launch|other", "target": "18%"}],
  "triggers": [{"trigger": "what must happen for the story to play out", "expected_by": "Q4 FY27"}],
  "tailwinds": ["sector or policy tailwinds, naming the policy or scheme"],
  "headwinds": ["..."],
  "policy": ["government policy / regulation and its effect"],
  "risks": ["..."],
  "tone": "confident|steady|cautious|defensive"
}"""

VIEW_SCHEMA = """{
  "what_it_does": "one line",
  "sector": "sector and sub-segment",
  "sector_drivers": ["tailwinds with the policy or scheme behind them"],
  "story": "2-3 lines: what has to happen for the story to play out, and by when (quarter / year)",
  "triggers": [{"trigger": "...", "expected_by": "Q4 FY27", "status": "on_track|delayed|done|at_risk|unclear"}],
  "watch": ["the metrics or events to watch next quarter"],
  "delivery": "how often management delivered what it said over these quarters, with the count",
  "trend": "2 lines: how the numbers and the insights moved across the quarters",
  "risks": ["..."],
  "one_line": "the latest view in at most 20 words"
}"""


def _key(period: str | None) -> str:
    return period_end(period) or "0000-00-00"


# ── context handed to the model ─────────────────────────────────────────────

def _report_mtime(folder: str | None, name: str) -> float | None:
    p = COMPANIES_DIR / folder / "reports" / f"{name}.json" if folder else None
    return p.stat().st_mtime if p and p.exists() else None


def _report(folder: str | None, name: str) -> dict:
    if not folder:
        return {}
    p = COMPANIES_DIR / folder / "reports" / f"{name}.json"
    try:
        d = json.loads(p.read_text(errors="replace")) if p.exists() else {}
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def ckey(text: str | None) -> str:
    """Comparable form of a commitment's text (the model may echo it with the target or punctuation)."""
    t = re.split(r"\s+—\s+target|→", str(text or ""), maxsplit=1)[0].lower()
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t)                                   # "[Q1 FY26] …" context tag
    t = re.sub(r"\s*\(?\bby\s+((q[1-4]|h[12])\s*)?fy\s*'?\d{2,4}\)?\s*\.?$", "", t)   # "… (by Q3 FY26)"
    return re.sub(r"[^a-z0-9%]+", " ", t).strip()[:160]


def _same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a.startswith(b[:30]) or b.startswith(a[:30]):
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / max(1, len(ta | tb)) >= 0.6


def real_checks(conn: sqlite3.Connection, sid: str, checks: list, verdict: str | None) -> tuple[list, str | None]:
    """Keep only checks of commitments management actually made (the model sometimes judges a generic
    "earlier targets"); with none left there is nothing to be ahead of or behind."""
    known = [ckey(c["what"]) for c in open_commitments(conn, sid, limit=60)]
    known += [ckey(r["commitment"]) for r in conn.execute(
        "SELECT DISTINCT commitment FROM commitment_checks WHERE sid=?", (sid,))]
    given = [c for c in checks if isinstance(c, dict) and c.get("commitment")]
    keep = [c for c in given if any(_same(ckey(c["commitment"]), k) for k in known)]
    if not keep:
        return [], ("no_commitments" if verdict else verdict)
    if len(keep) < len(given):          # the model's verdict leaned on checks that were dropped
        st = {str(c.get("status") or "unclear").lower() for c in keep}
        verdict = ("behind" if st & {"behind", "missed"} else "ahead" if st == {"ahead"} else
                   "in_line" if st <= {"on_track", "in_line", "delivered"} else "mixed")
    return keep, verdict


def open_commitments(conn: sqlite3.Connection, sid: str, limit: int = 12) -> list[dict]:
    """Commitments still open: from earlier quarterly notes, else from the earlier concall analysis."""
    closed = [ckey(r["commitment"]) for r in conn.execute(
        "SELECT DISTINCT commitment FROM commitment_checks WHERE sid=? AND status IN ('delivered','missed','changed')", (sid,))]

    def is_closed(k: str) -> bool:
        return any(_same(k, c) for c in closed)
    out: list[dict] = []
    seen: set[str] = set()
    for r in conn.execute("SELECT period, note FROM company_notes WHERE sid=? ORDER BY period_end DESC", (sid,)):
        note = json.loads(r["note"] or "{}")
        for c in note.get("commitments") or []:
            d = c if isinstance(c, dict) else {"what": str(c)}
            what = d.get("what")
            k = ckey(what)
            if what and k not in seen and not is_closed(k):
                seen.add(k)
                out.append({"said_in": r["period"], "what": what, "by_when": d.get("by_when"),
                            "target": d.get("target"), "metric": d.get("metric")})
    if not out:
        s = conn.execute("SELECT folder FROM stocks WHERE sid=?", (sid,)).fetchone()
        ta = _report(s["folder"] if s else None, "transcript_analysis")
        fe = ta.get("forward_estimates") or {}
        for k in ("revenue_targets", "earnings_guidance", "capex_plans"):
            for x in (fe.get(k) or [])[:3]:
                txt = x if isinstance(x, str) else " — ".join(str(v) for v in x.values() if isinstance(v, (str, int, float)))
                if not is_closed(ckey(txt)):
                    out.append({"said_in": "earlier concalls", "what": txt[:200], "by_when": None, "target": None})
        for t in ((ta.get("guidance_evolution") or {}).get("topics") or [])[:6]:
            tl = t.get("timeline") or []
            if tl:
                txt = f"{t.get('topic')}: {tl[-1].get('what_was_said')}"[:220]
                if not is_closed(ckey(txt)):
                    out.append({"said_in": tl[-1].get("quarter"), "what": txt, "by_when": None, "target": None})
    return out[:limit]


def numbers_table(conn: sqlite3.Connection, sid: str, n: int = 6) -> str:
    from . import score

    s = conn.execute("SELECT * FROM stocks WHERE sid=?", (sid,)).fetchone()
    if not s:
        return ""
    q = score.quarterly(conn, s)
    if not q.get("labels"):
        return ""
    rows = []
    for i in range(max(0, len(q["labels"]) - n), len(q["labels"])):
        def v(key, fmt="{:,.1f}"):
            x = (q.get(key) or [None] * len(q["labels"]))[i]
            return fmt.format(x) if x is not None else "—"
        rows.append(f"{q['labels'][i]}: sales {v('sales')}, OPM {v('opm', '{:.1f}%')}, PAT {v('pat')}, EPS {v('eps', '{:.2f}')}")
    return f"Recent quarters ({q['source']}, ₹ Cr):\n" + "\n".join(rows)


def context_block(conn: sqlite3.Connection, sid: str) -> str:
    """What the model needs to judge a results or concall document against the past."""
    lines = []
    commits = open_commitments(conn, sid)
    if commits:
        lines.append("OPEN COMMITMENTS from earlier quarters (check each against this document):")
        lines += [f"- [{c['said_in']}] {c['what']}" + (f" — target {c['target']}" if c.get("target") else "")
                  + (f" (by {c['by_when']})" if c.get("by_when") else "") for c in commits]
    nt = numbers_table(conn, sid)
    if nt:
        lines.append(nt)
    return "\n".join(lines)


# ── storing ─────────────────────────────────────────────────────────────────

def store_note(conn: sqlite3.Connection, sid: str, note: dict, *, source: str, doc_id: int | None, url: str | None) -> bool:
    period = norm_period(note.get("period"))
    if not period or not any(note.get(k) for k in ("business", "guidance", "commitments", "triggers")):
        return False
    note = {**note, "period": period}
    conn.execute(
        "INSERT INTO company_notes(sid, period, period_end, source, doc_id, url, note, created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(sid, period, source) DO UPDATE SET note=excluded.note, doc_id=excluded.doc_id, url=excluded.url, "
        "created_at=excluded.created_at",
        (sid, period, period_end(period), source, doc_id, url, json.dumps(note, ensure_ascii=False),
         now_ist().isoformat(timespec="seconds")))
    return True


def store_checks(conn: sqlite3.Connection, sid: str, checks: list, *, checked_in: str | None, doc_id: int | None,
                 url: str | None, verify=None) -> int:
    n = 0
    when = norm_period(checked_in) or (str(checked_in)[:40] if checked_in else None)
    for c in checks or []:
        if not isinstance(c, dict) or not c.get("commitment"):
            continue
        said = c.get("said_in")
        said = ", ".join(map(str, said)) if isinstance(said, list) else (str(said) if said is not None else None)
        said = (norm_period(said) or said)[:40] if said else None
        commitment = str(c["commitment"])[:300]
        status = str(c.get("status") or "pending").lower()[:12]
        if conn.execute("SELECT 1 FROM commitment_checks WHERE sid=? AND lower(commitment)=lower(?) AND "
                        "COALESCE(said_in,'')=COALESCE(?,'') AND COALESCE(checked_in,'')=COALESCE(?,'') AND status=?",
                        (sid, commitment, said, when, status)).fetchone():
            continue                           # the same check stored before (a re-run) — keep one
        quote = str(c.get("quote") or "")[:500]
        try:
            pg = int(float(c.get("page"))) if c.get("page") not in (None, "") else None
        except (TypeError, ValueError):
            pg = None
        score, page = verify(quote, pg) if verify and quote else (None, pg)
        conn.execute(
            "INSERT INTO commitment_checks(sid, commitment, said_in, checked_in, status, evidence, quote, page, verified, "
            "doc_id, url, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, commitment, said, when, status, str(c.get("evidence") or "")[:400], quote, page, score, doc_id, url,
             now_ist().isoformat(timespec="seconds")))
        n += 1
    return n


# ── the view ────────────────────────────────────────────────────────────────

VIEW_PROMPT = """You keep an investor's running notes on one Indian listed company. From the quarterly notes,
the commitment checks and the recent numbers below, write the company's current view as ONE JSON object:
{schema}

Rules: dense, numbers first, no filler words. Use only what the notes and numbers say. "story" must say what
has to happen and by when; if the notes give no timing, say "timing not given". This is research, not a
buy or sell recommendation.

COMPANY: {name} ({sid}) · sector: {sector}
{numbers}

QUARTERLY NOTES (oldest first):
{notes}

COMMITMENT CHECKS:
{checks}

PREVIOUS VIEW (update it, don't just copy it):
{previous}"""


# newest quarter first; for one quarter a concall / presentation note beats the back-filled one
NOTE_ORDER = "ORDER BY period_end DESC, (source='reports'), id DESC"


def refresh_view(conn: sqlite3.Connection, sid: str, settings: dict, *, backfill: bool = False) -> dict | None:
    from . import extract

    seen: set[str] = set()
    notes = [r for r in conn.execute(f"SELECT period, source, note FROM company_notes WHERE sid=? {NOTE_ORDER}", (sid,))
             if not (r["period"] in seen or seen.add(r["period"]))][:4]
    if not notes:
        return None
    s = conn.execute("SELECT name, sector FROM stocks WHERE sid=?", (sid,)).fetchone()
    checks = conn.execute("SELECT said_in, checked_in, commitment, status, evidence FROM commitment_checks WHERE sid=? "
                          "ORDER BY id DESC LIMIT 15", (sid,)).fetchall()
    prev = conn.execute("SELECT view FROM company_view WHERE sid=?", (sid,)).fetchone()
    prompt = VIEW_PROMPT.format(
        schema=VIEW_SCHEMA, name=s["name"] if s else sid, sid=sid, sector=(s["sector"] if s else "") or "n/a",
        numbers=numbers_table(conn, sid, 8) or "Recent quarters: not available",
        notes="\n".join(f"[{r['period']} · {r['source']}] {r['note']}" for r in reversed(notes)),
        checks="\n".join(f"- {c['commitment']} (said {c['said_in']}, checked {c['checked_in']}): {c['status']} — {c['evidence']}"
                         for c in checks) or "none yet",
        previous=prev["view"] if prev else "none")
    key = hashlib.sha256(prompt.encode()).hexdigest()[:24]
    view = extract.llm_json(conn, settings, prompt, purpose="notes_backfill" if backfill else "notes", sid=sid,
                            cache_name=f"view_{sid}_{key}", max_tokens=1500)
    if not isinstance(view, dict) or not view:
        return None
    conn.execute("DELETE FROM kv WHERE k=?", (f"view_stale:{sid}",))
    conn.execute("INSERT INTO company_view(sid, view, based_on, updated_at, model) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(sid) DO UPDATE SET view=excluded.view, based_on=excluded.based_on, updated_at=excluded.updated_at, "
                 "model=excluded.model",
                 (sid, json.dumps(view, ensure_ascii=False), json.dumps([r["period"] for r in notes]),
                  now_ist().isoformat(timespec="seconds"), settings["llm"]["client"]))
    return view


# ── back-fill from the reports already on disk ──────────────────────────────

BACKFILL_PROMPT = """Below are research reports an earlier workflow produced for one Indian listed company from its
concall transcripts, results and filings. Condense them into the investor's running notes. Return ONE JSON object:
{{"quarters": [up to 4 quarterly notes, oldest first, each shaped like {note_schema}],
  "commitment_checks": [{{"commitment": "...", "said_in": "Q1 FY26", "checked_in": "Q3 FY26",
                         "status": "delivered|partly|missed|pending|changed", "evidence": "one line"}}],
  "view": {view_schema}}}

Rules: only what the reports say; dense, numbers first, no filler. Write notes ONLY for these quarters, which
the reports cover: {allowed}. Label them exactly like that (e.g. "Q3 FY26" or "H1 FY26"). Never write a note for any other
quarter and never guess numbers for a quarter. Turn "guidance vs delivery" and "guidance evolution" into
commitments and checks. This is research, not a buy or sell recommendation.

COMPANY: {name} ({sid}) · sector: {sector}
{numbers}

CONCALL ANALYSIS (transcript_analysis.json):
{ta}

MASTER REPORT (selected fields):
{mr}

THESIS REVIEW (selected fields):
{tr}"""


def _trim(d: dict, keys: list[str] | None, limit: int) -> str:
    sub = {k: d[k] for k in (keys or list(d)) if k in d}
    s = json.dumps(sub, ensure_ascii=False)
    return s[:limit] + ("…" if len(s) > limit else "")


def report_quarters(ta: dict) -> list[str]:
    """Quarters the concall analysis actually covers (transcripts used, guidance timelines, delivery checks)."""
    qs = {norm_period(t.get("quarter")) for t in ta.get("transcripts_used") or [] if isinstance(t, dict)}
    for t in (ta.get("guidance_evolution") or {}).get("topics") or []:
        qs |= {norm_period(x.get("quarter")) for x in t.get("timeline") or [] if isinstance(x, dict)}
    qs |= {norm_period(g.get("quarter")) for g in ta.get("guidance_vs_delivery") or [] if isinstance(g, dict)}
    qs.discard(None)
    return sorted((q for q in qs if re.match(r"[QH][1-4] FY\d{2}$", q)), key=_key)


def backfill_one(conn: sqlite3.Connection, s: sqlite3.Row, settings: dict) -> dict | None:
    from . import extract

    ta = _report(s["folder"], "transcript_analysis")
    allowed = report_quarters(ta)[-4:] if ta else []
    if not allowed:          # no concall analysis yet (or none with a quarter): look again when the report changes
        return {"skip": "no report", "mtime": _report_mtime(s["folder"], "transcript_analysis")}
    mr = _report(s["folder"], "master_report")
    tr = _report(s["folder"], "thesis_review")
    prompt = BACKFILL_PROMPT.format(
        note_schema=NOTE_SCHEMA, view_schema=VIEW_SCHEMA, name=s["name"], sid=s["sid"], sector=s["sector"] or "n/a",
        allowed=", ".join(allowed),
        numbers=numbers_table(conn, s["sid"], 8) or "",
        ta=_trim(ta, None, 36000),
        mr=_trim(mr, ["investment_thesis", "industry_and_market_position", "key_catalysts", "key_risks", "monitoring_triggers",
                      "forward_revenue_path", "decision_framework", "sector_kpi_dashboard"], 14000),
        tr=_trim(tr, ["thesis_status", "thesis_status_rationale", "one_line_summary", "results_assessment",
                      "management_consistency", "thesis_delta", "forward_outlook"], 7000))
    key = hashlib.sha256(prompt.encode()).hexdigest()[:24]
    out = extract.llm_json(conn, settings, prompt, purpose="notes_backfill", sid=s["sid"],
                           cache_name=f"notesbf_{s['sid']}_{key}", max_tokens=4000)
    if not isinstance(out, dict) or not out:
        return None
    url = f"#/stock/{s['sid']}?tab=research"      # the stock page's Reports tab renders these reports
    good = [q for q in (out.get("quarters") or []) if isinstance(q, dict) and norm_period(q.get("period")) in allowed]
    n = sum(store_note(conn, s["sid"], q, source="reports", doc_id=None, url=url) for q in good)
    if n == 0:
        return None                               # nothing usable: the caller marks the company done
    for c in out.get("commitment_checks") or []:
        if isinstance(c, dict):
            store_checks(conn, s["sid"], [c], checked_in=c.get("checked_in"), doc_id=None, url=url)
    view = out.get("view")
    existing = conn.execute("SELECT 1 FROM company_view WHERE sid=?", (s["sid"],)).fetchone()
    if existing:
        conn.commit()                             # a live note wrote a view first: rewrite it with the history
        ok = None
        try:
            ok = refresh_view(conn, s["sid"], settings, backfill=True)
        except Exception as exc:
            conn.rollback()
            print(f"view refresh {s['sid']}: {exc.__class__.__name__}: {exc}")
        if not ok:                                # views_missing() rewrites it on a later tick
            conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                         (f"view_stale:{s['sid']}", json.dumps({"day": now_ist().date().isoformat()}),
                          now_ist().isoformat(timespec="seconds")))
    elif isinstance(view, dict) and view:
        conn.execute("INSERT INTO company_view(sid, view, based_on, updated_at, model) VALUES(?,?,?,?,?)",
                     (s["sid"], json.dumps(view, ensure_ascii=False), json.dumps([norm_period(q.get("period")) for q in good]),
                      now_ist().isoformat(timespec="seconds"), settings["llm"]["client"]))
    return {"notes": n}


def backfill(conn: sqlite3.Connection, settings: dict, deadline=lambda: True, limit: int = 6) -> dict:
    """Condense the existing reports into notes for companies not done yet (portfolio first).

    Done = notes from reports stored, or nothing to condense (permanent marker). A failed call is retried on a
    later day, three times at most; a spent budget just stops for today."""
    from . import extract

    today = now_ist().date().isoformat()
    out = {"companies": 0, "deferred_budget": False}
    reopen_markers(conn)
    rows = conn.execute(
        "SELECT s.* FROM stocks s WHERE s.folder IS NOT NULL AND (s.in_portfolio=1 OR s.in_watchlist=1 OR s.in_notes=1) "
        "AND NOT EXISTS (SELECT 1 FROM company_notes n WHERE n.sid=s.sid AND n.source='reports') "
        "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='notesbf_skip:' || s.sid) "
        "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='notesbf_retry:' || s.sid AND json_extract(v, '$.day') = ?) "
        "ORDER BY s.in_portfolio DESC, s.sid LIMIT ?", (today, limit)).fetchall()
    stamp = now_ist().isoformat(timespec="seconds")
    for s in rows:
        if not deadline():
            break
        if not extract.can_spend(conn, settings, 12000, 3000, "notes_backfill"):
            out["deferred_budget"] = True
            break
        try:
            res = backfill_one(conn, s, settings)
        except extract.BudgetExceeded:
            out["deferred_budget"] = True
            break
        except Exception as exc:              # network / API: retry another day, give up after three tries
            conn.rollback()
            print(f"notes back-fill {s['sid']}: {exc.__class__.__name__}: {exc}")
            r = conn.execute("SELECT v FROM kv WHERE k=?", (f"notesbf_retry:{s['sid']}",)).fetchone()
            tries = (json.loads(r["v"]).get("n", 0) if r else 0) + 1
            key = f"notesbf_skip:{s['sid']}" if tries >= 3 else f"notesbf_retry:{s['sid']}"
            conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                         (key, json.dumps({"n": tries, "day": today, "error": exc.__class__.__name__}), stamp))
            conn.commit()
            continue
        if res is None or res.get("skip"):   # nothing usable came back, or no concall analysis to condense yet
            mark = {"reason": "unusable"} if res is None else {"reason": res["skip"], "mtime": res.get("mtime")}
            conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                         (f"notesbf_skip:{s['sid']}", json.dumps(mark), stamp))
        else:
            out["companies"] += 1
        conn.commit()
    return out


def reopen_markers(conn: sqlite3.Connection) -> int:
    """A company skipped for having no concall analysis is back-filled once its report is (re)written."""
    n = 0
    for r in conn.execute("SELECT k, v FROM kv WHERE k LIKE 'notesbf_skip:%'").fetchall():
        try:
            mark = json.loads(r["v"] or "{}")
        except ValueError:
            mark = {}
        if not isinstance(mark, dict):          # markers from before 2026-11-02 hold a plain string
            mark = {"reason": str(mark)}
        if mark.get("reason") == "unusable":
            continue
        s = conn.execute("SELECT folder FROM stocks WHERE sid=?", (r["k"].split(":", 1)[1],)).fetchone()
        mt = _report_mtime(s["folder"] if s else None, "transcript_analysis")
        legacy = "mtime" not in mark                # markers from before 2026-10-20: look once more
        if mt is not None and (legacy or mark.get("mtime") is None or mt > mark["mtime"]):
            conn.execute("DELETE FROM kv WHERE k=?", (r["k"],))
            n += 1
    return n


def pending(conn: sqlite3.Connection) -> int:
    """Tracked companies the notes back-fill can still do today (same selection as backfill())."""
    today = now_ist().date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM stocks s WHERE s.folder IS NOT NULL AND (s.in_portfolio=1 OR s.in_watchlist=1 OR s.in_notes=1) "
        "AND NOT EXISTS (SELECT 1 FROM company_notes n WHERE n.sid=s.sid AND n.source='reports') "
        "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='notesbf_skip:' || s.sid) "
        "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='notesbf_retry:' || s.sid AND json_extract(v, '$.day') = ?)",
        (today,)).fetchone()[0]


def views_missing(conn: sqlite3.Connection, settings: dict, deadline=lambda: True, limit: int = 2) -> int:
    """Write a view for companies that have notes but none yet, or whose view missed the back-filled history."""
    from . import extract

    today = now_ist().date().isoformat()
    done = 0
    for r in conn.execute(
            "SELECT DISTINCT n.sid FROM company_notes n LEFT JOIN company_view v ON v.sid=n.sid "
            "WHERE (v.sid IS NULL OR EXISTS (SELECT 1 FROM kv WHERE k='view_stale:' || n.sid)) "
            "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='view_skip:' || n.sid AND json_extract(v, '$.day') = ?) LIMIT ?",
            (today, limit)).fetchall():
        if not deadline() or not extract.can_spend(conn, settings, 4000, 1000, "notes_backfill"):
            break
        try:
            ok = refresh_view(conn, r["sid"], settings, backfill=True)
        except Exception as exc:
            conn.rollback()
            ok = None
            print(f"view {r['sid']}: {exc.__class__.__name__}: {exc}")
        if not ok:
            conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES(?,?,?)",
                         (f"view_skip:{r['sid']}", json.dumps({"day": today}), now_ist().isoformat(timespec="seconds")))
        done += 1 if ok else 0
        conn.commit()
    return done


# ── read side (API) ─────────────────────────────────────────────────────────

def latest_status(conn: sqlite3.Connection, sid: str) -> dict[str, str]:
    """Each commitment once, at its latest status (pending → partly → delivered over the quarters)."""
    out: dict[str, str] = {}
    for r in conn.execute("SELECT commitment, status FROM commitment_checks WHERE sid=? ORDER BY id", (sid,)):
        k = ckey(r["commitment"])
        out[next((x for x in out if _same(x, k)), k)] = r["status"]
    return out


def company(conn: sqlite3.Connection, sid: str) -> dict:
    view = conn.execute("SELECT * FROM company_view WHERE sid=?", (sid,)).fetchone()
    notes = [dict(r) | {"note": json.loads(r["note"] or "{}")} for r in conn.execute(
        f"SELECT period, period_end, source, url, note, created_at FROM company_notes WHERE sid=? {NOTE_ORDER}", (sid,))]
    # one note per quarter: a fresh concall/presentation note beats the back-filled one
    seen, quarters = set(), []
    for n in notes:
        if n["period"] in seen:
            continue
        seen.add(n["period"])
        quarters.append(n)
    checks = [dict(r) for r in conn.execute(
        "SELECT commitment, said_in, checked_in, status, evidence, quote, page, verified, url FROM commitment_checks "
        "WHERE sid=? ORDER BY id DESC LIMIT 30", (sid,))]
    latest = latest_status(conn, sid)
    return {
        "sid": sid, "view": json.loads(view["view"]) if view else None,
        "view_updated": view["updated_at"] if view else None,
        "quarters": quarters[:4], "older": max(0, len(quarters) - 4),
        "checks": checks,
        "delivery": {k: sum(v == k for v in latest.values()) for k in ("delivered", "partly", "missed", "pending")},
    }


def listing(conn: sqlite3.Connection, scope_sql: str) -> list[dict]:
    rows = []
    for s in conn.execute(f"SELECT s.sid, s.name, s.sector, s.in_portfolio, s.in_watchlist, s.folder FROM stocks s WHERE {scope_sql} "
                          f"ORDER BY s.in_portfolio DESC, s.sid").fetchall():
        v = conn.execute("SELECT view, updated_at FROM company_view WHERE sid=?", (s["sid"],)).fetchone()
        last = conn.execute(f"SELECT period, source FROM company_notes WHERE sid=? {NOTE_ORDER} LIMIT 1",
                            (s["sid"],)).fetchone()
        n = conn.execute("SELECT COUNT(DISTINCT period) FROM company_notes WHERE sid=?", (s["sid"],)).fetchone()[0]
        latest = latest_status(conn, s["sid"])
        st = {k: sum(v == k for v in latest.values()) for k in ("delivered", "partly", "missed")}
        view = json.loads(v["view"]) if v else {}
        rows.append({"sid": s["sid"], "name": s["name"], "sector": s["sector"], "held": bool(s["in_portfolio"]),
                     "watch": bool(s["in_watchlist"]), "has_research": bool(s["folder"]),
                     "one_line": view.get("one_line"), "story": view.get("story"),
                     "next_trigger": next((t for t in view.get("triggers") or [] if isinstance(t, dict)
                                           and t.get("status") not in ("done",)), None),
                     "what_it_does": view.get("what_it_does"), "last_period": last["period"] if last else None,
                     "last_source": last["source"] if last else None, "quarters": n,
                     "delivered": st.get("delivered", 0), "partly": st.get("partly", 0), "missed": st.get("missed", 0),
                     "updated": v["updated_at"] if v else None})
    return rows
