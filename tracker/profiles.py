"""
Company profiles — what each company does, how its products are used and which end-markets it serves directly and
indirectly (Aeroflex makes stainless-steel hoses, but what moves it is AI data-centre liquid cooling) — shown on the
company page and used to match government policy to companies (policy.py).

Five a day (one per tick), written by the local model (localllm.py), least-invested holdings first, then the
watchlist. Sources: Screener's About, Key Points and sector / industry (one public page per company), the company view
and quarter notes the tracker keeps from concalls and presentations, the research reports under
data/companies/<folder>/reports/ and the latest filing summaries. A profile is rewritten after 180 days.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import timedelta

from .config import COMPANIES_DIR
from .util import Http, now_ist

PER_DAY = 5
MAX_AGE_DAYS = 180

PROMPT = """You write the company profile an investor keeps for an Indian listed company: what it really does and which
parts of the economy its revenue depends on — directly (who buys its products) and indirectly (what those buyers do
with them). Example of the depth wanted: a maker of stainless-steel flexible hoses whose growth comes from liquid-
cooling skids for AI data centres is exposed to "data centres / AI infrastructure", not just "steel products".

Company: {name} ({sid}). Listed sector: {sector}.

SOURCES
{sources}

Return ONE JSON object:
{{
  "description": "4-6 sentences: what it makes or does; its main products or services and how customers use them; who the
                  customers are; where revenue comes from (segments, exports) and what is driving growth now",
  "sector": "the sector it operates in, specific (e.g. 'Industrial flow components — stainless-steel hoses, bellows')",
  "business_type": "manufacturer | EPC / contractor | services | software | lender / NBFC | bank | trader | retailer | other",
  "products": ["up to 6 main products or services"],
  "end_markets": [{{"market": "…", "link": "direct | indirect", "how": "one line: how the product is used there",
                   "importance": "high | medium | low"}}],
  "exposures": ["up to 12 short tags for what moves revenue or costs: industries served, inputs and commodities,
                government programmes or policies, export markets — e.g. 'data centres', 'railways', 'EV batteries',
                'lithium', 'steel prices', 'PLI electronics', 'US exports'"],
  "sensitive_to": ["up to 5 risks: input prices, regulation, rates, a customer, a geography"]
}}
Rules: use only the sources; no figures that are not in them; prefer the newest concall or presentation facts for what
drives growth; say "not stated" rather than guess. JSON only."""


def _text(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def screener(http: Http, stock: sqlite3.Row) -> dict:
    """About, Key Points and the sector chain from the company's public Screener page."""
    code = stock["nse_symbol"] or stock["bse_code"]
    if not code:
        return {}
    for path in (f"/company/{code}/consolidated/", f"/company/{code}/"):
        r = http.get(f"https://www.screener.in{path}")
        if r.status_code != 200:
            continue
        t = r.text
        about = re.search(r'class="sub show-more-box about"[^>]*>(.*?)</div>', t, re.S)
        keyp = re.search(r'class="sub commentary always-show-more-box"[^>]*>(.*?)</div>', t, re.S)
        chain = {lab: _text(m.group(1)) for lab in ("Broad Sector", "Sector", "Broad Industry", "Industry")
                 for m in [re.search(rf'title="{lab}"[^>]*>(.*?)</a>', t, re.S)] if m}
        out = {"about": re.sub(r"\s*\[\d+\]", "", _text(about.group(1))) if about else "",
               "key_points": re.sub(r"\s*\[\d+\]", "", _text(keyp.group(1))) if keyp else "",
               "sector_chain": chain, "url": f"https://www.screener.in{path}"}
        if out["about"] or out["key_points"]:
            return out
    return {}


def _read(path, n: int) -> str:
    try:
        return path.read_text(errors="replace")[:n]
    except OSError:
        return ""


def gather(conn: sqlite3.Connection, http: Http, stock: sqlite3.Row) -> tuple[str, dict]:
    """The source text for the prompt, and what was used."""
    used: dict = {}
    parts = []
    scr = screener(http, stock)
    if scr:
        used["screener"] = scr["url"]
        chain = " › ".join(scr["sector_chain"].values())
        parts.append(f"[Screener] {scr['about']}\nKey points: {scr['key_points']}" + (f"\nClassification: {chain}" if chain else ""))
    v = conn.execute("SELECT view FROM company_view WHERE sid=?", (stock["sid"],)).fetchone()
    if v and v["view"]:
        try:
            view = json.loads(v["view"])
        except ValueError:
            view = {}
        keep = {k: view.get(k) for k in ("what_it_does", "sector", "sector_drivers", "story", "one_line") if view.get(k)}
        if keep:
            used["company_view"] = True
            parts.append("[Tracker's view from concalls and presentations] " + json.dumps(keep, ensure_ascii=False)[:2500])
    notes = conn.execute("SELECT period, note FROM company_notes WHERE sid=? ORDER BY period_end DESC LIMIT 2",
                         (stock["sid"],)).fetchall()
    for n in notes:
        try:
            note = json.loads(n["note"] or "{}")
        except ValueError:
            continue
        bits = {k: note.get(k) for k in ("business", "guidance", "tailwinds", "policy") if note.get(k)}
        if bits:
            used.setdefault("notes", []).append(n["period"])
            parts.append(f"[Quarter note {n['period']}] " + json.dumps(bits, ensure_ascii=False)[:1500])
    if stock["folder"]:
        rep = COMPANIES_DIR / stock["folder"] / "reports"
        for f, n in (("master_report.md", 2500), ("growth_signals.md", 1500)):
            t = _read(rep / f, n)
            if t.strip():
                used.setdefault("reports", []).append(f)
                parts.append(f"[Research report {f}] {t}")
    sums = [json.loads(r["summary"]) for r in conn.execute(
        "SELECT summary FROM documents WHERE sid=? AND summary IS NOT NULL AND backfill=0 ORDER BY filed_at DESC LIMIT 4",
        (stock["sid"],)) if r["summary"]]
    if sums:
        used["filings"] = len(sums)
        parts.append("[Latest filing summaries] " + " | ".join(" ".join(s) for s in sums)[:2500])
    return "\n\n".join(parts), used


def due(conn: sqlite3.Connection, limit: int = PER_DAY) -> list[str]:
    """Companies without a fresh profile: holdings from the least invested up, then the watchlist."""
    stale = (now_ist() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT s.sid, s.in_portfolio, COALESCE((SELECT SUM(h.invested) FROM holdings h WHERE h.sid=s.sid AND h.quantity>0), 0) inv "
        "FROM stocks s WHERE (s.in_portfolio=1 OR s.in_watchlist=1) AND NOT EXISTS "
        "(SELECT 1 FROM company_profiles p WHERE p.sid=s.sid AND p.updated_at >= ?) "
        "ORDER BY s.in_portfolio DESC, inv ASC, s.sid LIMIT ?", (stale, limit)).fetchall()
    return [r["sid"] for r in rows]


def write(conn: sqlite3.Connection, settings: dict, http: Http, sid: str) -> dict:
    from . import localllm

    stock = conn.execute("SELECT * FROM stocks WHERE sid=?", (sid,)).fetchone()
    sources, used = gather(conn, http, stock)
    if not sources:
        return {"sid": sid, "skipped": "no sources"}
    prompt = PROMPT.format(name=stock["name"], sid=sid, sector=stock["sector"] or "not stated", sources=sources[:12000])
    data = localllm.chat_json(conn, settings, prompt, purpose="profile", sid=sid, max_tokens=1500,
                              system="You are a careful equity analyst. Output one strict JSON object only — no prose, "
                                     "no code fences.")
    if not data.get("description"):
        return {"sid": sid, "failed": "empty answer"}
    stamp = now_ist().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO company_profiles(sid, description, sector, business_type, products, end_markets, exposures, "
        "sensitive_to, sources, model, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(sid) DO UPDATE SET description=excluded.description, sector=excluded.sector, "
        "business_type=excluded.business_type, products=excluded.products, end_markets=excluded.end_markets, "
        "exposures=excluded.exposures, sensitive_to=excluded.sensitive_to, sources=excluded.sources, "
        "model=excluded.model, updated_at=excluded.updated_at",
        (sid, str(data["description"])[:2000], str(data.get("sector") or "")[:200], str(data.get("business_type") or "")[:60],
         json.dumps(data.get("products") or [], ensure_ascii=False), json.dumps(data.get("end_markets") or [], ensure_ascii=False),
         json.dumps(data.get("exposures") or [], ensure_ascii=False), json.dumps(data.get("sensitive_to") or [], ensure_ascii=False),
         json.dumps(used, ensure_ascii=False), localllm.LOCAL_PREFIX + settings["llm"]["local_model"], stamp, stamp))
    conn.commit()
    return {"sid": sid, "written": True}


def run(conn: sqlite3.Connection, settings: dict, http: Http, limit: int = 1) -> dict:
    """At most `limit` profiles now and PER_DAY a day, only when the local model is up."""
    from . import localllm

    today = now_ist().date().isoformat()
    done_today = conn.execute("SELECT COUNT(*) FROM company_profiles WHERE substr(updated_at,1,10)=?", (today,)).fetchone()[0]
    if done_today >= PER_DAY:
        return {"skipped": f"{PER_DAY} written today"}
    if not localllm.available(settings):
        return {"skipped": "local model not running"}
    out = []
    for sid in due(conn, min(limit, PER_DAY - done_today)):
        try:
            out.append(write(conn, settings, http, sid))
        except Exception as exc:
            conn.rollback()
            out.append({"sid": sid, "failed": f"{exc.__class__.__name__}: {exc}"[:200]})
    return {"profiles": out}


def get(conn: sqlite3.Connection, sid: str) -> dict | None:
    r = conn.execute("SELECT * FROM company_profiles WHERE sid=?", (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("products", "end_markets", "exposures", "sensitive_to", "sources"):
        try:
            d[k] = json.loads(d[k] or "null")
        except ValueError:
            d[k] = None
    return d
