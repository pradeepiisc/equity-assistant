"""
Government policy from PIB (Press Information Bureau), every 3 hours from 07:00 to 23:30:

  1. the day's release list (allRel.aspx?reg=48&lang=1 — every ministry) → policy_releases, new release ids only
  2. releases from the Cabinet and the economic ministries, or whose title names a scheme, an outlay, a duty, a
     tariff, a PLI, an incentive … → the release text (PressReleasePage.aspx)
  3. the local model (localllm.py) reads it against the tracked companies — their profile (profiles.py) where one
     is written, else the company view from concalls, else the listed sector — and names the companies the policy
     can move: positive / negative, how strongly, and the channel ("capital subsidy for lithium processing plants:
     it is building one"), or none
  4. one "policy" event per company named (high for a holding with a strong link), linked to the release

No paid LLM: when the local model isn't running, releases wait (unread) and are matched on a later pass.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime

from .db import add_event
from .util import Http, now_ist

BASE = "https://www.pib.gov.in"
LIST_URL = f"{BASE}/allRel.aspx?reg=48&lang=1"
RELEASE_URL = BASE + "/PressReleasePage.aspx?PRID={prid}&reg=48&lang=1"
EVERY_MIN = 180

ECON_MINISTRY = re.compile(
    r"cabinet|finance|commerce|industry|mines|coal|power|renewable|petroleum|steel|heavy industries|electronics|"
    r"information technology|telecom|communications|railways|road transport|highways|shipping|ports|civil aviation|"
    r"defence|chemicals|fertili[sz]ers|textiles|food processing|agriculture|housing|urban|jal shakti|water|msme|"
    r"micro, small|pharmaceuticals|health|new and renewable|niti aayog|corporate affairs|consumer affairs|"
    r"competition commission|sebi|rbi|reserve bank|cooperation|tourism|space|atomic|earth sciences|"
    r"science & technology|statistics", re.I)
ECON_TITLE = re.compile(
    r"scheme|outlay|crore|approv|polic|tariff|duty|customs|gst|tax|pli|incentive|subsidy|capex|procure|tender|"
    r"auction|block|licen|regulat|notif|export|import|investment|fund|mission|project|corridor|capacity|plant|"
    r"mineral|battery|semiconductor|electronic|solar|wind|hydrogen|nuclear|rail|road|port|airport|shipbuild|defence|"
    r"msp|fertili[sz]er|pharma|textile|steel|coal|oil|gas|ethanol|sugar|telecom|5g|data centre|data center|ai\b", re.I)
NOT_ECON = re.compile(r"greet|condol|birth anniversary|death anniversary|pays tribute|wishes|meets|interact|"
                      r"yoga|award ceremony|quiz|exhibition|festival", re.I)
NEVER_ECON = re.compile(r"\bmela\b|agniveer|\bcourts?\b|pocso|sports|\bgames\b|swachh|special campaign|site cleaning|"
                        r"scrap disposal|statistical|e-auction of land|visitors", re.I)

PROMPT = """You are an equity analyst. A Government of India press release is below, followed by the companies an
investor tracks (portfolio and watchlist), each with what it does and the markets it serves.

Name only the companies whose revenue, costs or competitive position this policy can move in a way an investor
would act on, through a concrete channel: the company does the activity the policy funds or regulates (a direct
beneficiary or target), supplies the beneficiaries (equipment, materials, EPC, services), or buys from / competes
with them so its costs or prices change. Not "the economy grows" and not a loose sector match. If none, return [].
- Enforcement against particular firms or products (licence suspensions, penalties, recalls, approvals of one
  deal): name only companies the release itself names. MoUs, agreements, visits, campaigns, reviews and statistics
  change no money flow: return [] unless they fund or mandate something specific.
- strength "high" only when the effect is direct and material to the company's revenue or costs; "medium" for a clear
  but smaller or later effect; "low" otherwise. A proposal, draft rule or consultation (not yet in force) is at most
  "medium". At most 8 companies, strongest first.

RELEASE ({ministry}, {posted})
{title}
{text}

COMPANIES (sid | name | what it does / markets)
{companies}

Return ONE JSON object:
{{
  "relevant": true|false,
  "policy": "one line: what changes, the money and the period",
  "sectors": ["sectors the policy touches"],
  "companies": [{{"sid": "exactly as listed", "impact": "positive | negative | mixed",
                 "strength": "high | medium | low",
                 "channel": "direct | supplier | customer | competitor",
                 "reason": "one or two lines: how the policy reaches this company's business, naming its product or market"}}]
}}
JSON only."""


def _text(x: str) -> str:
    x = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", x or "", flags=re.S)
    x = html.unescape(re.sub(r"<[^>]+>", "\n", x))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", x)).strip()


def parse_list(page: str) -> list[dict]:
    """(prid, ministry, title) of each release on the day's list."""
    out = []
    for block in re.split(r"<h3[^>]*>", page)[1:]:
        ministry = _text(block.split("</h3>", 1)[0])
        for m in re.finditer(r"<a[^>]*title='([^']*)'[^>]*href='/PressReleaseDetail\.aspx\?PRID=(\d+)", block):
            out.append({"prid": int(m.group(2)), "ministry": ministry, "title": html.unescape(m.group(1)).strip()})
    return out


def release_text(http: Http, prid: int) -> tuple[str, str | None]:
    """(body text, posted-on ISO time) of one release."""
    r = http.get(RELEASE_URL.format(prid=prid))
    if r.status_code != 200:
        return "", None
    t = _text(r.text)
    posted = None
    m = re.search(r"Posted On:\s*(\d{1,2} [A-Z]{3} \d{4}) (\d{1,2}:\d{2})\s*([AP]M)", t)
    if m:
        try:
            posted = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y %I:%M %p").isoformat() + "+05:30"
        except ValueError:
            posted = None
    body = t
    i = t.find("Posted On:")
    if i >= 0:
        body = t[i:]
    j = body.find("(Release ID:")
    if j >= 0:
        body = body[:j]
    body = re.sub(r"^Posted On:[^\n]*\n(?:[^\n]*by PIB[^\n]*\n)?", "", body).strip()
    return body, posted


def economic(r: dict) -> bool:
    title = r["title"]
    if NEVER_ECON.search(title) or (NOT_ECON.search(title) and not re.search(r"\bscheme\b|outlay|cabinet approves", title, re.I)):
        return False
    return bool(ECON_MINISTRY.search(r["ministry"]) and ECON_TITLE.search(title)) or \
        bool(re.search(r"cabinet approves|outlay|crore|scheme|pli|tariff|duty", title, re.I))


def company_lines(conn: sqlite3.Connection) -> list[str]:
    """One line per tracked company: its profile when written, else the company view, else the listed sector."""
    out = []
    for s in conn.execute("SELECT s.sid, s.name, s.sector, s.in_portfolio, p.description, p.sector psector, p.exposures, "
                          "p.end_markets, json_extract(v.view, '$.what_it_does') what, json_extract(v.view, '$.sector') vsector "
                          "FROM stocks s LEFT JOIN company_profiles p ON p.sid=s.sid LEFT JOIN company_view v ON v.sid=s.sid "
                          "WHERE s.in_portfolio=1 OR s.in_watchlist=1 ORDER BY s.sid"):
        if s["description"]:
            ex = ", ".join((json.loads(s["exposures"] or "[]") or [])[:10])
            mk = "; ".join(f"{m.get('market')} ({m.get('link')})" for m in (json.loads(s["end_markets"] or "[]") or [])[:5]
                           if isinstance(m, dict))
            what = f"{s['psector']}. Markets: {mk}. Exposures: {ex}. {s['description'][:120]}"
        elif s["what"]:
            what = f"{s['vsector'] or s['sector'] or ''}. {s['what']}"
        else:
            what = s["sector"] or "sector not known"
        what = re.sub(r"\s+", " ", what)[:300]
        out.append(f"{s['sid']} | {s['name']} | {what}")
    return out


def match(conn: sqlite3.Connection, settings: dict, rel: sqlite3.Row) -> dict:
    from . import localllm

    lines = company_lines(conn)
    prompt = PROMPT.format(ministry=rel["ministry"], posted=(rel["posted_at"] or "")[:16], title=rel["title"],
                           text=(rel["text"] or "")[:7000], companies="\n".join(lines))
    data = localllm.chat_json(conn, settings, prompt, purpose="policy", max_tokens=2500, num_ctx=32768,
                              system="You are a careful equity analyst. Output one strict JSON object only — no prose, "
                                     "no code fences.")
    known = {ln.split(" | ", 1)[0] for ln in lines}
    data["companies"] = [c for c in data.get("companies") or [] if isinstance(c, dict) and c.get("sid") in known][:8]
    return data


STRENGTH_RANK = {"high": 3, "medium": 2, "low": 1}


def _events(conn: sqlite3.Connection, rel: sqlite3.Row, data: dict) -> int:
    """One card per release: the companies it can move, a row each with the reason, most affected first."""
    companies = data.get("companies") or []
    if not companies:
        return 0
    names = {r["sid"]: (r["name"], r["in_portfolio"]) for r in conn.execute("SELECT sid, name, in_portfolio FROM stocks")}
    rows = []
    for c in companies:
        strength, impact = (c.get("strength") or "low").lower(), (c.get("impact") or "mixed").lower()
        channel = (c.get("channel") or "").lower()
        name, held = names.get(c["sid"], (c["sid"], 0))
        rows.append({"sid": c["sid"], "name": name, "held": bool(held), "impact": impact, "strength": strength,
                     "channel": channel, "reason": c.get("reason") or "",
                     "tone": "pos" if impact == "positive" else "neg" if impact == "negative" else None,
                     "_rank": (STRENGTH_RANK.get(strength, 0), channel == "direct", bool(held))})
    rows.sort(key=lambda r: r["_rank"], reverse=True)
    rows = rows[:10]
    for r in rows:
        r.pop("_rank")
    # pushed only when a holding is hit directly, strongly and one way; otherwise it waits in the inbox
    strong_held = [r for r in rows if r["held"] and r["strength"] == "high" and r["channel"] == "direct"
                   and r["impact"] in ("positive", "negative")]
    sev = "high" if strong_held else "medium" if any(r["strength"] in ("high", "medium") for r in rows) else "low"
    n_held = sum(1 for r in rows if r["held"])
    title = f"Policy: {rel['title']}"[:200] + f" — {len(rows)} compan{'y' if len(rows) == 1 else 'ies'}" + \
        (f", {n_held} held" if n_held else "")
    table = {"columns": [{"key": "sid", "label": "Company"}, {"key": "impact", "label": "Impact", "tone": True},
                         {"key": "strength", "label": "Strength"}, {"key": "channel", "label": "How"},
                         {"key": "reason", "label": "Why", "wide": True}],
             "rows": rows, "note": f"{rel['ministry']} · PIB release {rel['prid']}"}
    eid = add_event(conn, sid=None, ts=rel["posted_at"] or rel["fetched_at"], kind="policy", type="policy_release",
                    severity=sev, title=title, detail=data.get("policy") or "", url=RELEASE_URL.format(prid=rel["prid"]),
                    source=f"PIB · {rel['ministry']}", dedupe=f"pib:{rel['prid']}",
                    data={"prid": rel["prid"], "sids": [r["sid"] for r in rows], "table": table})
    return 1 if eid else 0


def pending(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM policy_releases WHERE relevant=1 AND matched_at IS NULL AND text IS NOT NULL"
                        ).fetchone()[0]


def run(conn: sqlite3.Connection, http: Http, settings: dict, fetch: bool = True, max_match: int = 1) -> dict:
    """Fetch the list and new release texts (`fetch`), then match up to `max_match` unmatched releases with the local
    model — one a tick, so a 2-minute local read never holds the tick for long."""
    from . import localllm

    stamp = now_ist().isoformat(timespec="seconds")
    items = []
    if fetch:
        r = http.get(LIST_URL)
        if r.status_code != 200:
            return {"error": f"list HTTP {r.status_code}"}
        items = parse_list(r.text)
    new = 0
    for it in items:
        if conn.execute("SELECT 1 FROM policy_releases WHERE prid=?", (it["prid"],)).fetchone():
            continue
        rel = economic(it)
        if rel and conn.execute("SELECT 1 FROM policy_releases WHERE title=? AND relevant=1", (it["title"],)).fetchone():
            rel = False          # the Cabinet's release reposted by the ministry: matched once
        text, posted = release_text(http, it["prid"]) if rel else ("", None)
        conn.execute("INSERT INTO policy_releases(prid, ministry, title, url, posted_at, text, relevant, fetched_at) "
                     "VALUES(?,?,?,?,?,?,?,?)", (it["prid"], it["ministry"], it["title"], RELEASE_URL.format(prid=it["prid"]),
                                                 posted, text or None, 1 if rel else 0, stamp))
        new += 1
    conn.commit()
    out = {"listed": len(items), "new": new, "matched": 0, "events": 0}
    pending = conn.execute("SELECT * FROM policy_releases WHERE relevant=1 AND matched_at IS NULL AND text IS NOT NULL "
                           "ORDER BY prid DESC LIMIT ?", (max_match,)).fetchall()
    if pending and not localllm.available(settings):
        out["waiting"] = f"{len(pending)} release(s): local model not running"
        return out
    for rel in pending:
        try:
            data = match(conn, settings, rel)
        except Exception as exc:
            out.setdefault("failed", []).append(f"{rel['prid']}: {exc.__class__.__name__}")
            continue
        conn.execute("UPDATE policy_releases SET matches=?, model=?, matched_at=? WHERE prid=?",
                     (json.dumps(data, ensure_ascii=False), localllm.LOCAL_PREFIX + settings["llm"]["local_model"],
                      now_ist().isoformat(timespec="seconds"), rel["prid"]))
        out["events"] += _events(conn, rel, data)
        out["matched"] += 1
        conn.commit()
    return out
