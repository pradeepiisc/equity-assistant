"""
The local-model comparison week. Every weekday evening the day's filings are read again by each model in
`llm_eval.models` (only inside `llm_eval.window`, so the fans stay quiet during the day), another model
(`llm_eval.judge_client`) grades all the reads blind against the filing's pages, and the night's table is written to
data/tracker/evals/<mon>-<d>-eval.md. After `llm_eval.weekdays_needed` evenings, final() decides once.

  nightly(settings)   local reads inside the window, then grading and the night's report
  final(settings)     the overall table and the decision (runs once)
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import date, datetime

from .config import TRACKER_DIR
from .db import kv_get, kv_set, session
from .util import now_ist

EVAL_DIR = TRACKER_DIR / "evals"
LOCAL_LOCK = TRACKER_DIR / "localllm.lock"     # one local-model task at a time (tick or this)
GPT = "gpt-4.1"
FINAL_KEY = "llm_eval_final"

CHECKLIST = [
    "revenue with YoY and/or QoQ change",
    "expenses, EBITDA or operating margin, with the change",
    "net profit (PAT) and EPS, with YoY",
    "the period (e.g. Q2 FY27) and consolidated vs standalone",
    "order book, order inflow or new orders",
    "capacity, expansion plans or capex",
    "guidance or targets with numbers",
    "tailwinds (demand, pricing, policy, new markets)",
    "headwinds (costs, competition, delays, regulation)",
    "one-offs, exceptional items, dilution, pledges or other red flags",
]

JUDGE_PROMPT = """You are grading short summaries of an Indian listed company's stock-exchange filing. They are written
for a small-cap investor who trades on momentum and must decide whether to buy, hold or exit. Grade each summary only
against the filing's pages below; you don't know which model wrote which, and the order is random.

Company: {company} ({sid}). Document: {kind}, filed {filed}, period {period}.

CHECKLIST: what a good summary covers when the filing contains it
{checklist}

First decide which checklist items this filing actually contains ("applicable"). Then, for each summary:
- figures: how many numeric figures it states (₹ amounts, %, growth rates, margins, bps, multiples, EPS, volumes)
- wrong_figures: how many of those are wrong against the pages: wrong value, wrong period, wrong unit, standalone and
  consolidated mixed up, or a YoY / QoQ / margin that doesn't follow from the table (recompute them yourself)
- checklist_covered: the applicable item numbers it covers
- usefulness 1-5: how much it helps the buy / hold / exit decision
- readability 1-5: how quickly it can be read and taken in
- clarity 1-5: unambiguous; each figure's period and basis are clear
- misleading: how many statements would mislead an investor (wrong direction, overstated, cherry-picked, unsupported)
- worst: a short phrase on its biggest problem, or ""
Then rank all summaries from best to worst.

Reply with one JSON object only:
{{"applicable": [1, 2], "grades": {{"S1": {{"figures": 0, "wrong_figures": 0, "checklist_covered": [1], "usefulness": 0,
"readability": 0, "clarity": 0, "misleading": 0, "worst": ""}}}}, "ranking": ["S1"], "note": "one sentence on the main
difference between them"}}

FILING PAGES
{pages}

SUMMARIES
{summaries}
"""


def _cfg(settings: dict) -> dict:
    return settings["llm_eval"]


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def in_window(settings: dict, now: datetime | None = None) -> bool:
    now = now or now_ist()
    a, b = (_hm(x) for x in _cfg(settings)["window"])
    return now.weekday() <= 5 and a <= (now.hour, now.minute) < b


def label(variant: str, models: list[str]) -> str:
    if variant == GPT:
        return "A: gpt-4.1, as the dashboard shows now"
    name = variant.removeprefix("local:")
    i = models.index(name) if name in models else len(models)
    return f"{['C1', 'D', 'E', 'F'][min(i, 3)]}: local {name}, one-shot"


def _docs(conn, settings: dict, until: str):
    cfg = _cfg(settings)
    kinds = cfg["kinds"]
    return conn.execute(
        f"SELECT * FROM documents WHERE backfill=0 AND status='extracted' AND kind IN ({','.join('?' * len(kinds))}) "
        "AND summary IS NOT NULL AND substr(filed_at, 1, 10) BETWEEN ? AND ? ORDER BY filed_at",
        (*kinds, cfg["from"], min(until, cfg["until"]))).fetchall()


# ── reads ───────────────────────────────────────────────────────────────────

def _read_all(settings: dict, today: str, force: bool) -> dict:
    """Each local model reads every due filing it hasn't read, one model at a time (unloaded after), until the
    window closes. A filing whose read fails is stored with the error, so it is graded as an empty summary."""
    from . import extract, localllm
    from .jobs import tick_lock

    out: dict = {}
    for model in _cfg(settings)["models"]:
        if not localllm.available(settings, model):
            out[model] = "not installed (ollama pull " + model + ")"
            continue
        with session() as conn:
            todo = [d["id"] for d in _docs(conn, settings, today) if not conn.execute(
                "SELECT 1 FROM eval_reads WHERE doc_id=? AND model=?", (d["id"], localllm.LOCAL_PREFIX + model)).fetchone()]
        done = failed = 0
        for doc_id in todo:
            if not (force or in_window(settings)):
                break
            with tick_lock("llm_eval", LOCAL_LOCK) as got:
                if not got:                       # a tick's local task (movers, policy, profile): wait for it
                    time.sleep(20)
                    todo.append(doc_id)
                    continue
                with session() as conn:
                    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
                    try:
                        r = extract.shadow_read(conn, doc, settings, model=model)
                        done += 1
                        print(f"  {model} {doc['sid']} {doc['kind']} {r['secs']}s")
                    except Exception as exc:
                        failed += 1
                        msg = f"{exc.__class__.__name__}: {exc}"[:300]
                        print(f"  {model} {doc['sid']} failed: {msg}")
                        conn.execute("INSERT OR REPLACE INTO eval_reads(doc_id, model, summary, numcheck, secs, created_at) "
                                     "VALUES(?,?,?,?,?,?)", (doc_id, localllm.LOCAL_PREFIX + model, "[]",
                                                             json.dumps({"error": msg}), None,
                                                             now_ist().isoformat(timespec="seconds")))
                        conn.commit()
        localllm.unload(settings, model)
        out[model] = {"read": done, "failed": failed, "left": len(set(todo)) - done - failed}
    return out


# ── grading ─────────────────────────────────────────────────────────────────

def _variants(conn, doc) -> dict[str, list[str]]:
    v = {GPT: json.loads(doc["summary"] or "[]")}
    for r in conn.execute("SELECT model, summary FROM eval_reads WHERE doc_id=? ORDER BY model", (doc["id"],)):
        v[r["model"]] = json.loads(r["summary"] or "[]")
    return v


def judge(conn, settings: dict, doc) -> dict:
    """One blind grading call for every read of the filing; stored in eval_judgements keyed by variant name."""
    from llm.client import get_model_name

    from . import extract

    variants = _variants(conn, doc)
    keys = sorted(variants)
    random.Random(doc["id"]).shuffle(keys)
    labels = {f"S{i + 1}": k for i, k in enumerate(keys)}
    from .company_notes import context_block

    _, keep, pages = extract.build_prompt(conn, doc, settings)
    body = "\n\n".join(f"=== Page {p} ===\n{pages[p - 1].strip()}" for p in keep)[:90000]
    if any(extract._wants(doc)):        # the readers also had the earlier quarters' notes: a figure from there is fine
        ctx = context_block(conn, doc["sid"])
        if ctx:
            body += ("\n\nCONTEXT FROM EARLIER QUARTERS (given to every summary writer too; a figure taken from here "
                     "is correct only if the summary makes clear it is not this filing's)\n" + ctx)
    stock = conn.execute("SELECT name FROM stocks WHERE sid=?", (doc["sid"],)).fetchone()
    prompt = JUDGE_PROMPT.format(
        company=stock["name"] if stock else doc["sid"], sid=doc["sid"], kind=doc["kind"], filed=doc["filed_at"][:10],
        period=doc["period"] or "?", checklist="\n".join(f"{i}. {c}" for i, c in enumerate(CHECKLIST, 1)), pages=body,
        summaries="\n\n".join(f"{s}:\n" + ("\n".join(f"- {x}" for x in variants[k]) or "(empty)")
                              for s, k in labels.items()))
    client = _cfg(settings)["judge_client"]
    js = {**settings, "llm": {**settings["llm"], "client": client}}
    data = extract.llm_json(conn, js, prompt, purpose="eval", sid=doc["sid"], max_tokens=8000,
                            cache_name=f"eval_{doc['sha256']}_{hashlib.sha256(prompt.encode()).hexdigest()[:10]}",
                            system="You are a strict, fair grader of financial summaries. Output strict JSON only.")
    grades = {labels[s]: g for s, g in (data.get("grades") or {}).items() if s in labels}
    out = {"judge": get_model_name(client), "applicable": data.get("applicable") or [],
           "grades": grades, "ranking": [labels[s] for s in data.get("ranking") or [] if s in labels],
           "note": data.get("note") or "", "summaries": variants}
    conn.execute("INSERT OR REPLACE INTO eval_judgements(doc_id, day, data, created_at) VALUES(?,?,?,?)",
                 (doc["id"], doc["filed_at"][:10], json.dumps(out, ensure_ascii=False),
                  now_ist().isoformat(timespec="seconds")))
    conn.commit()
    return out


def _judge_all(settings: dict, today: str) -> dict:
    """Grade every filing that has at least one local model read. A filing graded before another model
    finishes is graded again, with all reads, once the newer read is recorded."""
    from . import localllm
    from .extract import BudgetExceeded

    done = failed = 0
    with session() as conn:
        for doc in _docs(conn, settings, today):
            have = {r["model"] for r in conn.execute("SELECT model FROM eval_reads WHERE doc_id=?", (doc["id"],))}
            if not have:
                continue
            old = conn.execute("SELECT data FROM eval_judgements WHERE doc_id=?", (doc["id"],)).fetchone()
            if old and have <= set(json.loads(old["data"])["summaries"]):
                continue
            try:
                judge(conn, settings, doc)
                done += 1
            except BudgetExceeded as exc:
                print(f"  grading stopped: {exc}")
                break
            except Exception as exc:
                failed += 1
                print(f"  grading {doc['sid']} failed: {exc.__class__.__name__}: {exc}")
    return {"graded": done, "failed": failed}


# ── tables ──────────────────────────────────────────────────────────────────

def _secs(conn, doc_id: int, variant: str) -> float | None:
    if variant == GPT:
        r = conn.execute("SELECT ms FROM llm_calls WHERE doc_id=? AND purpose='filing' AND ok=1 ORDER BY id DESC LIMIT 1",
                         (doc_id,)).fetchone()
        return r["ms"] / 1000 if r else None
    r = conn.execute("SELECT secs FROM eval_reads WHERE doc_id=? AND model=?", (doc_id, variant)).fetchone()
    return r["secs"] if r else None


def aggregate(conn, rows) -> dict[str, dict]:
    """Per variant: totals over the graded filings (rows of eval_judgements)."""
    agg: dict[str, dict] = {}
    for row in rows:
        j = json.loads(row["data"])
        applicable = len(j["applicable"])
        best = j["ranking"][0] if j["ranking"] else None
        for v, g in j["grades"].items():
            a = agg.setdefault(v, {"docs": 0, "figures": 0, "wrong": 0, "covered": 0, "applicable": 0, "use": 0.0,
                                   "read": 0.0, "clar": 0.0, "misleading": 0, "best": 0, "secs": []})
            a["docs"] += 1
            a["figures"] += int(g.get("figures") or 0)
            a["wrong"] += int(g.get("wrong_figures") or 0)
            a["covered"] += len(set(g.get("checklist_covered") or []) & set(j["applicable"]))
            a["applicable"] += applicable
            a["use"] += float(g.get("usefulness") or 0)
            a["read"] += float(g.get("readability") or 0)
            a["clar"] += float(g.get("clarity") or 0)
            a["misleading"] += int(g.get("misleading") or 0)
            a["best"] += v == best
            s = _secs(conn, row["doc_id"], v)
            if s is not None:
                a["secs"].append(s)
    return agg


def _order(variants) -> list[str]:
    return sorted(variants, key=lambda v: (v != GPT, v))


def table(agg: dict[str, dict], models: list[str]) -> str:
    out = ["| Model used | Wrong figures | Checklist covered | Usefulness | Readability | Clarity | Misleading statements | Ranked best |",
           "|---|---|---|---|---|---|---|---|"]
    for v in _order(agg):
        a = agg[v]
        n = a["docs"] or 1
        out.append(f"| {label(v, models)} | {a['wrong']} / {a['figures']} | {a['covered']} / {a['applicable']} | "
                   f"{a['use'] / n:.1f} | {a['read'] / n:.1f} | {a['clar'] / n:.1f} | {a['misleading']} | {a['best']} |")
    out += ["", "| Model used | Filings | Avg seconds per filing | Slowest |", "|---|---|---|---|"]
    for v in _order(agg):
        s = agg[v]["secs"]
        out.append(f"| {label(v, models)} | {agg[v]['docs']} | {sum(s) / len(s):.0f} | {max(s):.0f} |" if s else
                   f"| {label(v, models)} | {agg[v]['docs']} | – | – |")
    return "\n".join(out)


def eval_path(d: date):
    return EVAL_DIR / f"{d.strftime('%b').lower()}-{d.day}-eval.md"


def report(settings: dict, day: str) -> str | None:
    """The night's file: the overall table, then each filing's reads side by side with their grades."""
    models = _cfg(settings)["models"]
    with session() as conn:
        rows = conn.execute("SELECT j.*, d.sid, d.kind, d.title, d.period, d.path FROM eval_judgements j "
                            "JOIN documents d ON d.id=j.doc_id WHERE j.day=? ORDER BY d.filed_at", (day,)).fetchall()
        if not rows:
            return None
        agg = aggregate(conn, rows)
    d = date.fromisoformat(day)
    judge_model = json.loads(rows[0]["data"]).get("judge", "")
    lines = [f"# Summary comparison: {d.strftime('%a %d %b %Y')}", "",
             f"{len(rows)} filings (results, press releases, presentations, transcripts), each read by every model "
             f"below and graded blind by {judge_model} against the filing's pages. Wrong figures = wrong / figures "
             f"stated; checklist covered = items covered / items the filing contains; usefulness, readability and "
             f"clarity are 1-5 averages; misleading statements are a total; ranked best = filings where it came first.",
             "", table(agg, models), "", "## Filings", ""]
    for r in rows:
        j = json.loads(r["data"])
        lines += [f"### {r['sid']}: {r['kind']} {r['period'] or ''}", "", f"{(r['title'] or '')[:200]}  ",
                  f"PDF: `{r['path']}`  ", f"Checklist items in the filing: {', '.join(map(str, j['applicable'])) or 'none'}  ",
                  f"Ranking: {' > '.join(label(v, models).split(':')[0] for v in j['ranking'])}  ",
                  f"Judge: {j['note']}", ""]
        for v in _order(j["summaries"]):
            g = j["grades"].get(v) or {}
            lines += [f"**{label(v, models)}**: wrong {g.get('wrong_figures', '?')}/{g.get('figures', '?')}, "
                      f"checklist {len(g.get('checklist_covered') or [])}, usefulness {g.get('usefulness', '?')}, "
                      f"readability {g.get('readability', '?')}, clarity {g.get('clarity', '?')}, "
                      f"misleading {g.get('misleading', '?')}" + (f"; worst: {g['worst']}" if g.get("worst") else "")]
            lines += [f"- {x}" for x in j["summaries"][v]] or ["- (empty)"]
            lines.append("")
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = eval_path(d)
    path.write_text("\n".join(lines) + "\n")
    return str(path)


# ── jobs ────────────────────────────────────────────────────────────────────

def nightly(settings: dict, force: bool = False) -> dict:
    """The 17:00 job: local reads until the window closes, then grading and tonight's report."""
    cfg = _cfg(settings)
    today = now_ist().date().isoformat()
    with session() as conn:
        if kv_get(conn, FINAL_KEY):
            return {"skipped": "comparison finished (eval-final ran)"}
        # the reads the tick made before the comparison moved to the evening count too
        conn.execute("INSERT OR IGNORE INTO eval_reads(doc_id, model, summary, numcheck, secs, created_at) "
                     "SELECT doc_id, model, summary, numcheck, secs, created_at FROM shadow_reads")
        conn.commit()
    if today < cfg["from"]:
        return {"skipped": f"comparison starts {cfg['from']}"}
    out = {"day": today, "reads": _read_all(settings, today, force) if force or in_window(settings) else "outside window"}
    out["grading"] = _judge_all(settings, today)
    out["report"] = report(settings, today)
    # also update the running cumulative final-eval.md table
    final_res = final(settings)
    out["final_status"] = final_res
    return out


def _wrong_figures_by_date(rows: list, models: list[str]) -> list[str]:
    """Section at the bottom of final-eval.md detailing exactly which figures each model got wrong per filing."""
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)

    lines = ["## Wrong Figures Analysis by Date", "",
             "Specific examples of filings where each model reported incorrect numbers, wrong periods, "
             "inverted YoY/QoQ directions, or unsupported figures as identified by blind evaluation against the source PDFs.", ""]

    for day in sorted(by_day.keys(), reverse=True):
        drows = by_day[day]
        err_rows = []
        for r in drows:
            j = json.loads(r["data"])
            if any((g.get("wrong_figures") or 0) > 0 for g in j["grades"].values()):
                err_rows.append((r, j))

        d_obj = date.fromisoformat(day)
        lines.append(f"### {d_obj.strftime('%a %d %b %Y')} ({len(err_rows)} of {len(drows)} filings with errors)")
        lines.append("")

        if not err_rows:
            lines.append("_No incorrect figures reported on this date._\n")
            continue

        for r, j in err_rows:
            sid = r["sid"]
            kind = r["kind"].replace("_", " ").title()
            period = f" ({r['period']})" if r["period"] else ""
            lines.append(f"#### {sid}: {kind}{period}")
            lines.append(f"**PDF:** `{r['path']}`  ")
            if j.get("note"):
                lines.append(f"**Judge Evaluation:** {j['note']}  ")

            for v in _order(j["summaries"]):
                g = j["grades"].get(v) or {}
                w = g.get("wrong_figures") or 0
                tot = g.get("figures") or 0
                lbl = label(v, models)
                worst = g.get("worst")
                if w > 0:
                    lines.append(f"- **{lbl}**: **{w}/{tot} wrong figures**" + (f" — *Issue: {worst}*" if worst else ""))
                    for s_line in j["summaries"][v]:
                        lines.append(f"  - `{s_line}`")
                else:
                    lines.append(f"- **{lbl}**: 0 wrong figures ({tot} figures verified)")
            lines.append("")

    return lines


def final(settings: dict, force: bool = False) -> dict:
    """Updates final-eval.md with the cumulative table across all graded evenings.
    Records the final decision in kv once weekdays_needed evenings are graded."""
    cfg = _cfg(settings)
    models = cfg["models"]
    with session() as conn:
        already_decided = kv_get(conn, FINAL_KEY)
        if already_decided and not force:
            return {"skipped": "already decided", **already_decided}
        days = [r["day"] for r in conn.execute("SELECT DISTINCT day FROM eval_judgements WHERE day >= ? ORDER BY day",
                                               (cfg["from"],)) if date.fromisoformat(r["day"]).weekday() <= 5]
        rows = conn.execute("SELECT j.*, d.sid, d.kind, d.title, d.period, d.path FROM eval_judgements j "
                            "JOIN documents d ON d.id=j.doc_id WHERE j.day >= ? ORDER BY j.day DESC, d.filed_at",
                            (cfg["from"],)).fetchall()
        if not rows:
            return {"waiting": "nothing graded"}
        agg = aggregate(conn, rows)
        per_day = {d: aggregate(conn, [r for r in rows if r["day"] == d]) for d in days}

    is_complete = force or len(days) >= cfg["weekdays_needed"] or now_ist().date().isoformat() > cfg["until"]
    decision, why = decide(agg)
    status_msg = f"Completed ({len(days)} evenings graded)" if is_complete else f"In progress ({len(days)} of {cfg['weekdays_needed']} evenings graded)"
    lines = [f"# Summary comparison: cumulative ({days[0] if days else '?'} to {days[-1] if days else '?'})", "",
             f"**Status: {status_msg}**  ",
             f"{len(rows)} filings over {len(days)} evenings, graded blind by "
             f"{json.loads(rows[0]['data']).get('judge', '')}. Daily files: "
             + ", ".join(f"`{eval_path(date.fromisoformat(d)).name}`" for d in days), "",
             table(agg, models), "", "## By evening", "",
             "| Evening | Model used | Filings | Wrong figures | Checklist covered | Usefulness | Misleading | Ranked best |",
             "|---|---|---|---|---|---|---|---|"]
    for d, a in per_day.items():
        for v in _order(a):
            x = a[v]
            n = x["docs"] or 1
            lines.append(f"| {d} | {label(v, models).split(':')[0]} | {x['docs']} | {x['wrong']} / {x['figures']} | "
                         f"{x['covered']} / {x['applicable']} | {x['use'] / n:.1f} | {x['misleading']} | {x['best']} |")
    lines += ["", "## Current Standing & Decision", "", f"**{label(decision, models)}**", "", why, "",
              "Rule: a local model is chosen only if, against gpt-4.1, its wrong-figure rate is at most 1 point "
              "higher, its misleading statements per filing at most 0.1 higher, its checklist coverage at most "
              "3 points lower and its usefulness at most 0.1 lower. Among the local models that pass, the fastest "
              "wins (less heat). Otherwise gpt-4.1 stays.", ""]
    if decision != GPT:
        lines += ["Next step (not applied automatically): point the live filing read at the local model "
                  f"(`llm.local_model: {decision.removeprefix('local:')}` in tracker.yaml and a local path in "
                  "`extract.call_llm`). It takes minutes per filing instead of seconds, so alerts from filings arrive later.", ""]
    
    # Add the detailed wrong figures breakdown by date
    lines += _wrong_figures_by_date(rows, models)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / "final-eval.md"
    path.write_text("\n".join(lines) + "\n")
    result = {"status": status_msg, "days_graded": len(days), "weekdays_needed": cfg["weekdays_needed"],
              "decision": decision, "why": why, "report": str(path), "updated_at": now_ist().isoformat(timespec="seconds")}
    if is_complete:
        with session() as conn:
            kv_set(conn, FINAL_KEY, result)
            conn.commit()
    return result


def decide(agg: dict[str, dict]) -> tuple[str, str]:
    def m(v):
        a = agg[v]
        n = a["docs"] or 1
        return {"wrong": a["wrong"] / (a["figures"] or 1), "mis": a["misleading"] / n,
                "cov": a["covered"] / (a["applicable"] or 1), "use": a["use"] / n,
                "secs": sum(a["secs"]) / len(a["secs"]) if a["secs"] else float("inf")}
    if GPT not in agg:
        return GPT, "gpt-4.1 has no graded filings; keeping it."
    g = m(GPT)
    passing = []
    notes = []
    for v in _order(agg):
        if v == GPT:
            continue
        x = m(v)
        fails = [f for f, bad in (("wrong figures", x["wrong"] > g["wrong"] + 0.01),
                                  ("misleading statements", x["mis"] > g["mis"] + 0.1),
                                  ("checklist coverage", x["cov"] < g["cov"] - 0.03),
                                  ("usefulness", x["use"] < g["use"] - 0.1)) if bad]
        notes.append(f"- {v.removeprefix('local:')}: wrong {x['wrong']:.1%} vs {g['wrong']:.1%}, misleading/filing "
                     f"{x['mis']:.2f} vs {g['mis']:.2f}, coverage {x['cov']:.0%} vs {g['cov']:.0%}, usefulness "
                     f"{x['use']:.2f} vs {g['use']:.2f}, {x['secs']:.0f}s vs {g['secs']:.0f}s a filing"
                     + (f"; falls short on {', '.join(fails)}" if fails else "; matches gpt-4.1"))
        if not fails:
            passing.append((x["secs"], v))
    if passing:
        best = min(passing)[1]
        return best, "\n".join(notes) + f"\n\n{best.removeprefix('local:')} matches gpt-4.1 and is the fastest that does."
    return GPT, "\n".join(notes) + "\n\nNo local model matches gpt-4.1 on every measure; gpt-4.1 stays."
