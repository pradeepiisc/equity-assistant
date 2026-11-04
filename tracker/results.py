"""
One results record — and one event — per company per quarter.

Results arrive as several filings the same day (the results PDF, a press release, the
standalone and consolidated XBRL, a newspaper extract, a presentation). Instead of an event
for each, the tracker keeps one `results_reviews` row per (stock, quarter) and one event:

  • numbers — exact figures from NSE's XBRL when it arrives (no LLM), else the figures the AI
    read of the results PDF found; YoY / QoQ against the same basis where history allows
  • review — from the AI read: 3–5 summary lines, anomalies, and how the quarter compares with
    what management committed to earlier (from earlier notes / concall analysis)

Whichever arrives first creates the event; the other updates it (and marks it unread again).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from .db import add_event
from .util import now_ist

REVISION = re.compile(r"revis|correct|corrigend|errat|rectif|restat", re.I)


def quarter_of(period_end: str) -> str:
    d = date.fromisoformat(period_end)
    fy = d.year + 1 if d.month >= 4 else d.year
    q = (d.month - 4) % 12 // 3 + 1
    return f"Q{q} FY{str(fy)[2:]}"


_QEND = {"mar": (3, 31), "jun": (6, 30), "sep": (9, 30), "dec": (12, 31)}
_FY = re.compile(r"FY\s*'?(\d{4})\s*[-–/]\s*(\d{2,4})\b|FY\s*'?(\d{2})\s*[-–/]\s*(\d{2})\b|FY\s*'?(\d{4}|\d{2})(?!\d)", re.I)
_CAL = re.compile(r"\b(mar|jun|sep|dec)[a-z]*\.?(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?[\s\-'’]*(\d{4})\b"
                  r"|\b(mar|jun|sep|dec)[a-z]*[\-'’ ](\d{2})\b(?![,\d])"
                  r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(mar|jun|sep|dec)[a-z]*,?\s+(\d{4})\b", re.I)


def norm_period(period: str | None) -> str | None:
    """Normalise the many ways a period is written to 'Q2 FY27', 'H1 FY27' or 'FY27'.

    'Q2FY27', 'Q2 FY 27', 'Q2 FY2026-27', 'Q2 FY26-27' → Q2 FY27 (a range ends in its second year);
    'Sep 2026', 'Sep-26', 'quarter ended September 30, 2026', '30 Sep 2026' → Q2 FY27; 'H1 FY27';
    'FY2026' / 'FY26' → FY26."""
    s = period or ""
    fy = _FY.search(s)
    yy = None
    if fy:
        yy = (fy.group(2) or fy.group(4) or fy.group(5))[-2:]
    q = re.search(r"\bQ\s*([1-4])(?!\d)", s, re.I)
    if q and yy:
        return f"Q{q.group(1)} FY{yy}"
    h = re.search(r"\bH\s*([12])(?!\d)", s, re.I)
    if h and yy:
        return f"H{h.group(1)} FY{yy}"
    c = _CAL.search(s)
    if c and not (fy and fy.start() < c.start()):     # 'FY24 (guided in Feb/Mar 2025)' is FY24, not a quarter
        mon = (c.group(1) or c.group(3) or c.group(5)).lower()
        raw = c.group(2) or c.group(4) or c.group(6)
        yr = int(raw) if len(raw) == 4 else 2000 + int(raw)
        m, d = _QEND[mon]
        return quarter_of(date(yr, m, d).isoformat())
    return f"FY{yy}" if yy else None


def period_end(period: str | None) -> str | None:
    p = norm_period(period)
    if not p:
        return None
    m = re.match(r"([QH])([1-4]) FY(\d{2})", p)
    if not m:
        fy = 2000 + int(p[2:])
        return f"{fy}-03-31"
    kind, n, fy = m.group(1), int(m.group(2)), 2000 + int(m.group(3))
    if kind == "H":
        return f"{fy - 1}-09-30" if n == 1 else f"{fy}-03-31"
    return {1: f"{fy - 1}-06-30", 2: f"{fy - 1}-09-30", 3: f"{fy - 1}-12-31", 4: f"{fy}-03-31"}[n]


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None


def xbrl_numbers(conn: sqlite3.Connection, sid: str, period_end_: str, basis: str) -> dict | None:
    """Quarter figures from financials_q with YoY / QoQ on the same basis when those quarters are stored."""
    row = conn.execute("SELECT * FROM financials_q WHERE sid=? AND period_end=? AND months=3 AND basis=?",
                       (sid, period_end_, basis)).fetchone()
    if not row or row["revenue_cr"] is None:
        return None
    d = date.fromisoformat(period_end_)
    yoy_end = date(d.year - 1, d.month, d.day).isoformat()
    qoq = conn.execute("SELECT * FROM financials_q WHERE sid=? AND months=3 AND basis=? AND period_end < ? "
                       "ORDER BY period_end DESC LIMIT 1", (sid, basis, period_end_)).fetchone()
    yoy = conn.execute("SELECT * FROM financials_q WHERE sid=? AND months=3 AND basis=? AND period_end=?",
                       (sid, basis, yoy_end)).fetchone()
    margin = round(row["ebitda_cr"] / row["revenue_cr"] * 100, 1) if row["ebitda_cr"] is not None and row["revenue_cr"] else None

    def m_of(r):
        return round(r["ebitda_cr"] / r["revenue_cr"] * 100, 1) if r and r["ebitda_cr"] is not None and r["revenue_cr"] else None
    out = {
        "source": "xbrl", "basis": basis, "revenue_cr": row["revenue_cr"], "ebitda_cr": row["ebitda_cr"],
        "ebitda_margin_pct": margin, "pat_cr": row["pat_cr"], "eps": row["eps"],
        "revenue_yoy_pct": _pct(row["revenue_cr"], yoy["revenue_cr"]) if yoy else None,
        "revenue_qoq_pct": _pct(row["revenue_cr"], qoq["revenue_cr"]) if qoq else None,
        "pat_yoy_pct": _pct(row["pat_cr"], yoy["pat_cr"]) if yoy else None,
        "pat_qoq_pct": _pct(row["pat_cr"], qoq["pat_cr"]) if qoq else None,
        "ebitda_margin_yoy_bp": round((margin - m_of(yoy)) * 100) if margin is not None and m_of(yoy) is not None else None,
        "pat_prev_year_cr": yoy["pat_cr"] if yoy else None,
        "url": row["url"],
    }
    anomalies = []
    if row["other_income_cr"] and row["pbt_cr"] and row["pbt_cr"] > 0 and row["other_income_cr"] / row["pbt_cr"] > 0.3:
        anomalies.append(f"Other income ₹{row['other_income_cr']:,.1f} Cr is {row['other_income_cr'] / row['pbt_cr'] * 100:.0f}% "
                         f"of PBT — profit leans on non-operating income")
    # standalone only: consolidated pat_cr is the owners' share, which isn't PBT less tax when minorities exist
    if basis == "standalone" and row["pbt_cr"] and row["pat_cr"] is not None and row["pbt_cr"] > 0:
        tax = 1 - row["pat_cr"] / row["pbt_cr"]
        if tax < 0.12 or tax > 0.40:
            anomalies.append(f"Effective tax rate about {tax * 100:.0f}% (PBT ₹{row['pbt_cr']:,.1f} Cr → PAT ₹{row['pat_cr']:,.1f} Cr)")
    out["anomalies"] = anomalies
    return out


def _ebitda(x: dict) -> float | None:
    """EBITDA before exceptional items, excluding other income — as the XBRL path computes it."""
    f = {k: x.get(k) for k in ("pbt_cr", "finance_cost_cr", "depreciation_cr", "other_income_cr", "exceptional_cr")}
    try:
        f = {k: (float(v) if v not in (None, "") else None) for k, v in f.items()}
    except (TypeError, ValueError):
        return None
    if f["pbt_cr"] is None or f["finance_cost_cr"] is None or f["depreciation_cr"] is None:
        return None
    return round(f["pbt_cr"] - (f["exceptional_cr"] or 0) + f["finance_cost_cr"] + f["depreciation_cr"]
                 - (f["other_income_cr"] or 0), 2)


def pdf_ebitda(res: dict, n: dict) -> dict:
    """EBITDA and its margin for a results PDF: computed from the printed P&L lines, never the model's own sum.

    A model-given EBITDA is kept only when the document printed it and its margin agrees with EBITDA / revenue;
    the YoY change only from the year-ago quarter's printed lines."""
    rev = n.get("revenue_cr")
    out = {"ebitda_cr": None, "ebitda_margin_pct": None, "ebitda_margin_yoy_bp": None}
    e = _ebitda(res)
    if e is not None and rev:
        out.update(ebitda_cr=e, ebitda_margin_pct=round(e / rev * 100, 1), ebitda_basis="computed")
    elif n.get("ebitda_cr") is not None and rev:
        m = round(n["ebitda_cr"] / rev * 100, 1)
        if n.get("ebitda_margin_pct") is None or abs(n["ebitda_margin_pct"] - m) <= 0.5:
            out.update(ebitda_cr=n["ebitda_cr"], ebitda_margin_pct=m, ebitda_basis="printed")
    prev = res.get("prev_year") if isinstance(res.get("prev_year"), dict) else {}
    pe, pr = _ebitda(prev), prev.get("revenue_cr")
    try:
        pr = float(pr) if pr not in (None, "") else None
    except (TypeError, ValueError):
        pr = None
    if out.get("ebitda_basis") == "computed" and pe is not None and pr:   # both years on the same basis
        out["ebitda_margin_yoy_bp"] = round((out["ebitda_margin_pct"] - pe / pr * 100) * 100)
    return out


def _fmt_pct(v, unit="%"):
    return f"{v:+.0f}{unit}" if v is not None else None


def title_for(period: str, nums: dict | None, review: dict | None) -> str:
    n = headline_numbers(nums)
    if not n:
        line = (review or {}).get("headline") or ((review or {}).get("summary") or ["Results filed"])[0]
        return f"{period} results — {line}"[:220]
    parts = []
    if n.get("revenue_cr") is not None:
        ch = ", ".join(x for x in (f"{_fmt_pct(n.get('revenue_yoy_pct'))} YoY" if n.get("revenue_yoy_pct") is not None else None,
                                   f"{_fmt_pct(n.get('revenue_qoq_pct'))} {'HoH' if str(period).startswith('H') else 'QoQ'}"
                                   if n.get("revenue_qoq_pct") is not None else None) if x)
        parts.append(f"revenue ₹{n['revenue_cr']:,.0f} Cr" + (f" ({ch})" if ch else ""))
    if n.get("ebitda_margin_pct") is not None:
        bp = n.get("ebitda_margin_yoy_bp")
        parts.append(f"EBITDA margin {n['ebitda_margin_pct']:.1f}%" + (f" ({bp:+.0f} bp YoY)" if bp is not None else ""))
    if n.get("pat_cr") is not None:
        ch = f" ({_fmt_pct(n.get('pat_yoy_pct'))} YoY)" if n.get("pat_yoy_pct") is not None else ""
        parts.append(f"PAT ₹{n['pat_cr']:,.1f} Cr{ch}" if n["pat_cr"] >= 0 else f"net loss ₹{-n['pat_cr']:,.1f} Cr")
    verdict = (review or {}).get("verdict")
    tail = {"ahead": " — ahead of commitments", "in_line": " — in line with commitments", "behind": " — behind commitments",
            "mixed": " — mixed vs commitments"}.get(verdict or "", "")
    return f"{period} results: " + " · ".join(parts) + tail


def _short(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip(" —-")
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def headline_numbers(nums: dict | None) -> dict | None:
    """The numbers the headline uses: the company's own (results PDF, read and checked) when available —
    they carry YoY / QoQ — else the exact XBRL ones."""
    nums = nums or {}
    pdf, xbrl = nums.get("pdf"), nums.get("xbrl")
    if pdf and pdf.get("revenue_cr") is not None and (pdf.get("verified") or 0) >= 1.0:
        # revenue means the same in both sources; PAT and EBITDA legitimately differ (total vs owners', other income)
        if not xbrl or not xbrl.get("revenue_cr") or abs(pdf["revenue_cr"] - xbrl["revenue_cr"]) <= 0.01 * xbrl["revenue_cr"]:
            return pdf
    return xbrl or pdf


_TARGET = re.compile(r"\s+—\s+target")   # the model sometimes echoes the commitment with its target


def detail_for(nums: dict | None, review: dict | None) -> str:
    nums = nums or {}
    lines = [_short(x, 240) for x in list((review or {}).get("summary") or [])[:5]]
    unusual = [(a.get("text") or "") if isinstance(a, dict) else str(a) for a in (review or {}).get("anomalies") or []]
    unusual = [u for u in unusual if u]
    unusual += [a for a in (nums.get("xbrl") or {}).get("anomalies") or [] if not any(a[:30] in u for u in unusual)]
    if unusual:
        lines.append("Unusual: " + "; ".join(_short(u.rstrip(". "), 120) for u in unusual[:3]))
    checks = [c for c in (review or {}).get("vs_commitments") or [] if isinstance(c, dict) and c.get("commitment")]
    if checks:
        lines.append("vs commitments: " + "; ".join(f"{_short(_TARGET.split(c['commitment'])[0], 60)} → {c.get('status', 'unclear')}"
                                                    for c in checks[:4]))
    x, pdf = nums.get("xbrl"), nums.get("pdf")
    if x:
        bits = [f"revenue ₹{x['revenue_cr']:,.1f} Cr"]
        if pdf and pdf.get("revenue_cr"):
            same = abs(pdf["revenue_cr"] - x["revenue_cr"]) <= 0.01 * x["revenue_cr"]
            bits[0] += " (matches the PDF)" if same else f" (PDF says ₹{pdf['revenue_cr']:,.1f} Cr — check)"
        if x.get("ebitda_margin_pct") is not None:
            bits.append(f"EBITDA margin {x['ebitda_margin_pct']:.1f}% excl. other income")
        if x.get("pat_cr") is not None:
            bits.append(f"PAT to owners ₹{x['pat_cr']:,.1f} Cr")
        lines.append(f"NSE XBRL ({x.get('basis')}): " + ", ".join(bits))
    elif pdf and pdf.get("source") == "screener":
        lines.append("Numbers from Screener's results table (₹ Cr, rounded) — the BSE filing's AI read refines them")
    elif pdf:
        lines.append("Numbers read from the results PDF by AI and checked against its statement table"
                     if pdf.get("checked") == "table" else
                     "Numbers read from the results PDF by AI — check the PDF" if (pdf.get("verified") or 0) >= 1.0 else
                     "Numbers read from the results PDF by AI; the quoted line was not found word-for-word — check the PDF")
    return "\n".join(lines) if lines else "Results filed."


def tone_for(nums: dict | None, review: dict | None) -> str | None:
    """'strong' (growth, a turnaround or margin expansion), 'weak' (a fall), else None — drives ▲/▼ and the push."""
    n, review = headline_numbers(nums) or {}, review or {}
    ry, py, bp = n.get("revenue_yoy_pct"), n.get("pat_yoy_pct"), n.get("ebitda_margin_yoy_bp")
    prev = ((nums or {}).get("xbrl") or {}).get("pat_prev_year_cr")
    if review.get("verdict") == "behind" or (ry is not None and ry <= -10) or (py is not None and py <= -20):
        return "weak"
    if (ry is not None and py is not None and ry >= 15 and py >= 20) or (review.get("verdict") == "ahead" and (ry or 0) >= 0) \
            or (n.get("pat_cr") is not None and n["pat_cr"] > 0 and prev is not None and prev <= 0) \
            or (ry is not None and ry >= 10 and bp is not None and bp >= 200):
        return "strong"
    return None


def severity_for(nums: dict | None, review: dict | None) -> str:
    n, review = headline_numbers(nums) or {}, review or {}
    if tone_for(nums, review) == "strong":
        return "high"
    if review.get("verdict") == "behind" or any(isinstance(c, dict) and c.get("status") in ("missed", "behind")
                                                 for c in review.get("vs_commitments") or []):
        return "high"
    if (n.get("revenue_yoy_pct") is not None and n["revenue_yoy_pct"] <= -10) or \
            (n.get("pat_yoy_pct") is not None and n["pat_yoy_pct"] <= -20):
        return "high"
    if review.get("anomalies") or ((nums or {}).get("xbrl") or {}).get("anomalies"):
        return "high"
    return "medium"


def _source(nums: dict | None, review: dict | None) -> str:
    x = bool((nums or {}).get("xbrl"))
    if not x and ((nums or {}).get("pdf") or {}).get("source") == "screener":
        return "Results · Screener table (BSE)"
    return "Results · " + ("AI read + NSE XBRL" if review and x else "NSE XBRL" if x else "AI read of the results PDF")


def upsert(conn: sqlite3.Connection, sid: str, period: str, *, numbers: dict | None = None, review: dict | None = None,
           doc_id: int | None = None, url: str | None = None, ts: str | None = None, replace: bool = False,
           revised: bool = False) -> int | None:
    """Merge new numbers and/or a review into the quarter's record and (re)write its single event.

    `replace`: this source supersedes what is stored (a re-read of the same document, a re-filed XBRL);
    `revised`: a correction / revision — replaces too, and shows the event as unread again."""
    period = norm_period(period) or period
    row = conn.execute("SELECT * FROM results_reviews WHERE sid=? AND period=?", (sid, period)).fetchone()
    cur_n = json.loads(row["numbers"]) if row and row["numbers"] else {}
    if cur_n and ("pdf" not in cur_n and "xbrl" not in cur_n):     # older single-source shape
        cur_n = {cur_n.get("source", "xbrl"): cur_n}
    cur_r = json.loads(row["review"]) if row and row["review"] else None
    replace = replace or revised
    changed = False
    if numbers:
        slot = "xbrl" if numbers.get("source") == "xbrl" else "pdf"
        have = cur_n.get(slot)
        same_basis = have is not None and numbers.get("basis") == have.get("basis")
        if have is None or (numbers.get("basis") == "consolidated" and have.get("basis") != "consolidated") or \
                (same_basis and (replace or (numbers.get("url") != have.get("url")
                                             and (numbers.get("verified") or 0) >= (have.get("verified") or 0)))):
            changed = numbers != have
            cur_n[slot] = numbers          # whole: never one source's margin with another's YoY
    added_review = False
    # a later read whose numbers were checked against the statement replaces one that wasn't (the first read of a
    # scanned PDF could stick while a correct presentation read only changed the headline — AEROENTER Q2 FY27)
    if review and (not cur_r or replace or (review.get("checked") or 0) > (cur_r.get("checked") or 0)):
        added_review = not cur_r or review != {k: v for k, v in cur_r.items() if k != "revised"}
        cur_r = review
    stamp = now_ist().isoformat(timespec="seconds")
    data = {"period": period, "numbers": cur_n, "verdict": (cur_r or {}).get("verdict"), "doc_id": doc_id,
            "tone": tone_for(cur_n, cur_r),
            "revised": revised or (json.loads(row["review"] or "{}").get("revised") if row and row["review"] else False)}
    if revised and cur_r:
        cur_r = {**cur_r, "revised": True}
        title_prefix = "Revised · "
    else:
        title_prefix = ""
    title = title_prefix + ("Strong " if data["tone"] == "strong" else "") + title_for(period, cur_n, cur_r)
    detail, sev = detail_for(cur_n, cur_r), severity_for(cur_n, cur_r)
    link = url or (headline_numbers(cur_n) or {}).get("url") or ""
    event_id = row["event_id"] if row else None
    old_ev = conn.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone() if event_id else None
    if old_ev and old_ev["data"]:
        kept = (json.loads(old_ev["data"]) or {}).get("local_reads")
        if kept:
            data["local_reads"] = kept            # the local model's read of the same quarter (extract.shadow_read)
    if event_id and conn.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
        conn.execute("UPDATE events SET title=?, detail=?, severity=?, data=?, source=?, url=CASE WHEN ?='' THEN url ELSE ? END "
                     "WHERE id=?", (title, detail, sev, json.dumps(data, default=str), _source(cur_n, cur_r), link, link, event_id))
        if added_review or (revised and changed):   # new information on an event already seen: unread again
            conn.execute("UPDATE alerts SET read_at=NULL WHERE event_id=?", (event_id,))
    else:
        event_id = add_event(conn, sid=sid, ts=ts or stamp, kind="filing", type="results_review", severity=sev, title=title,
                             detail=detail, url=link, source=_source(cur_n, cur_r),
                             dedupe=f"results:{sid}:{period}", data=data)
        if event_id is None:
            r = conn.execute("SELECT id FROM events WHERE dedupe=?", (f"results:{sid}:{period}",)).fetchone()
            event_id = r["id"] if r else None
    conn.execute(
        "INSERT INTO results_reviews(sid, period, period_end, basis, numbers, review, event_id, doc_id, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(sid, period) DO UPDATE SET period_end=excluded.period_end, "
        "basis=excluded.basis, numbers=excluded.numbers, review=excluded.review, event_id=excluded.event_id, "
        "doc_id=CASE WHEN ? THEN excluded.doc_id ELSE COALESCE(results_reviews.doc_id, excluded.doc_id) END, "
        "updated_at=excluded.updated_at",
        (sid, period, period_end(period), (headline_numbers(cur_n) or {}).get("basis"), json.dumps(cur_n, default=str) if cur_n else None,
         json.dumps(cur_r, default=str) if cur_r else None, event_id, doc_id, stamp, 1 if (replace and doc_id) else 0))
    return event_id


def from_xbrl(conn: sqlite3.Connection, sid: str, period_end_: str, basis: str, url: str, ts: str) -> int | None:
    n = xbrl_numbers(conn, sid, period_end_, basis)
    if not n:
        return None
    return upsert(conn, sid, quarter_of(period_end_), numbers=n, url=url, ts=ts, replace=True)
