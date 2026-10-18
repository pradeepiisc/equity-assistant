"""
Personal research notes (e.g. from YouTube videos) as markdown files in notes/.

    ---
    title: Defence smallcaps with big order books
    source: https://www.youtube.com/watch?v=...
    channel: <channel name>
    date: 2026-10-11
    ---
    $ROSSTECH add — order book ~3x revenue, new aerospace plant by Q4. entry 520 target 900 sl 450
    $ZELIO watch: EV two-wheelers, wait for Q2 numbers
    - APOLLOPIPE: reduce, guidance deferred again

A stock is mentioned with `$SYMBOL` anywhere in a line, or as `- SYMBOL:` at the start
of a bullet. NSE symbols and BSE scrip codes both work. The first stance word after the
mention sets the stance; `entry/buy below`, `target/tgt`, `sl/stop` set price levels that
raise alerts when the closing price reaches them.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime

import yaml

from . import universe
from .config import NOTES_DIR
from .db import add_event, kv_get, kv_set
from .util import IST, now_ist

STANCES: list[tuple[str, tuple[str, ...]]] = [
    ("exit", ("exit", "sell all", "sell")),
    ("reduce", ("reduce", "trim", "book profit", "book profits", "partial exit", "lighten")),
    ("add", ("add more", "add-more", "add", "buy", "accumulate", "average up", "average down",
             "increase", "sip", "top up")),
    ("avoid", ("avoid", "stay away", "red flag")),
    ("hold", ("hold", "keep")),
    ("watch", ("watch", "track", "new idea", "idea", "discover", "research", "wait", "look at")),
]
_STANCE_RE = [(st, re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I))
              for st, words in STANCES]
_DOLLAR = re.compile(r"\$([A-Za-z0-9&\-]{2,20})")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+([A-Z0-9&\-]{2,20})\s*(?::|—|–|-\s)")
_LEVELS = {
    "entry": re.compile(r"\b(?:entry|buy\s+(?:below|under|near|around)|accumulate\s+below)\s*[:@]?\s*(?:rs\.?|₹)?\s*(\d[\d,]*(?:\.\d+)?)", re.I),
    "target": re.compile(r"\b(?:target|tgt|tp)\s*[:@]?\s*(?:rs\.?|₹)?\s*(\d[\d,]*(?:\.\d+)?)", re.I),
    "stop": re.compile(r"\b(?:sl|stop\s*loss|stop)\s*[:@]?\s*(?:rs\.?|₹)?\s*(\d[\d,]*(?:\.\d+)?)", re.I),
}
TEMPLATE = """---
title: {title}
source: {source}
channel: {channel}
date: {d}
---
{body}
"""


def _front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")
    return {}, text


def parse_mentions(body: str) -> list[dict]:
    out = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        found: list[tuple[int, str]] = [(m.end(), m.group(1).upper()) for m in _DOLLAR.finditer(line)]
        bm = _BULLET.match(line)
        if bm and not found:
            found.append((bm.end(1), bm.group(1).upper()))
        if not found:
            continue
        levels = {}
        for k, rx in _LEVELS.items():
            m = rx.search(line)
            if m:
                levels[k] = float(m.group(1).replace(",", ""))
        for pos, sym in found:
            stance = "mention"
            best = None
            for st, rx in _STANCE_RE:
                m = rx.search(line, pos)
                if m and (best is None or m.start() < best):
                    best, stance = m.start(), st
            out.append({"line_no": line_no, "raw_symbol": sym, "stance": stance,
                        "text": line.strip(), **levels})
    return out


def _signature() -> list:
    if not NOTES_DIR.exists():
        return [0, 0]
    files = [f for f in NOTES_DIR.glob("*.md") if f.name.lower() != "readme.md"]
    return [len(files), max((f.stat().st_mtime for f in files), default=0)]


def changed(conn: sqlite3.Connection) -> bool:
    return _signature() != kv_get(conn, "notes_sig")


def index(conn: sqlite3.Connection) -> dict:
    """(Re)index every note; resolves mentions and adds unseen stocks to the universe."""
    NOTES_DIR.mkdir(exist_ok=True)
    seen_ids = set()
    n_mentions = unresolved = 0
    for f in sorted(NOTES_DIR.glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        text = f.read_text(errors="replace")
        meta, body = _front_matter(text)
        nid = f.stem
        seen_ids.add(nid)
        d = meta.get("date")
        d = d.isoformat() if isinstance(d, date) else (str(d) if d else
             datetime.fromtimestamp(f.stat().st_mtime, IST).date().isoformat())
        conn.execute(
            "INSERT OR REPLACE INTO notes(id, path, title, source_url, channel, d, body, mtime) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (nid, str(f), str(meta.get("title") or nid), str(meta.get("source") or ""),
             str(meta.get("channel") or ""), d, body, f.stat().st_mtime),
        )
        conn.execute("DELETE FROM note_mentions WHERE note_id=?", (nid,))
        for m in parse_mentions(body):
            res = universe.resolve(conn, m["raw_symbol"])
            sid = None
            if res is not None:
                existing = conn.execute("SELECT sid FROM stocks WHERE sid=?", (res["sid"],)).fetchone()
                folder = universe.find_folder(m["raw_symbol"], res["sid"], res.get("bse_code") or "")
                sid = universe.upsert_stock(conn, res, folder=folder) if existing is None else res["sid"]
            else:
                unresolved += 1
            conn.execute(
                "INSERT OR REPLACE INTO note_mentions(note_id, line_no, raw_symbol, sid, stance, entry, "
                "target, stop, text) VALUES(?,?,?,?,?,?,?,?,?)",
                (nid, m["line_no"], m["raw_symbol"], sid, m["stance"], m.get("entry"), m.get("target"),
                 m.get("stop"), m["text"]),
            )
            n_mentions += 1
            if sid:
                add_event(
                    conn, sid=sid, ts=f"{d}T09:00:00+05:30", kind="note", type=f"note_{m['stance']}",
                    severity="info", title=f"Note: {m['stance']} — {meta.get('title') or nid}",
                    detail=m["text"][:500], source=str(meta.get("channel") or "note"),
                    url=str(meta.get("source") or ""), dedupe=f"note:{nid}:{m['line_no']}:{sid}",
                    data={"note_id": nid, "stance": m["stance"]}, backfill=True,
                )
    for row in conn.execute("SELECT id FROM notes").fetchall():
        if row["id"] not in seen_ids:
            conn.execute("DELETE FROM notes WHERE id=?", (row["id"],))
            conn.execute("DELETE FROM note_mentions WHERE note_id=?", (row["id"],))
    kv_set(conn, "notes_sig", _signature())
    return {"notes": len(seen_ids), "mentions": n_mentions, "unresolved": unresolved}


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "note"


def create(title: str, body: str, source: str = "", channel: str = "", d: str | None = None) -> str:
    NOTES_DIR.mkdir(exist_ok=True)
    d = d or now_ist().date().isoformat()
    base = f"{d}-{slugify(title)}"
    path = NOTES_DIR / f"{base}.md"
    i = 2
    while path.exists():
        path = NOTES_DIR / f"{base}-{i}.md"
        i += 1
    def q(v: str) -> str:
        return yaml.safe_dump(v, allow_unicode=True, default_style=None).strip().removesuffix("...").strip()
    path.write_text(TEMPLATE.format(title=q(title), source=q(source or ""), channel=q(channel or ""),
                                    d=d, body=body.strip() + "\n"))
    return path.stem


def check_levels(conn: sqlite3.Connection, live_since: str | None) -> int:
    """Raise an event when the latest close reaches a note's entry / target / stop level."""
    n = 0
    rows = conn.execute(
        "SELECT m.*, n.title, n.d AS note_d, t.close, t.prev_close, t.d AS bar_d FROM note_mentions m "
        "JOIN notes n ON n.id = m.note_id JOIN technicals t ON t.sid = m.sid "
        "WHERE m.sid IS NOT NULL AND (m.entry IS NOT NULL OR m.target IS NOT NULL OR m.stop IS NOT NULL)"
    ).fetchall()
    for r in rows:
        close, prev = r["close"], r["prev_close"]
        if close is None or prev is None or r["bar_d"] < (r["note_d"] or ""):
            continue
        checks = [
            ("entry", r["entry"], close <= (r["entry"] or 0) < prev, "reached your entry level"),
            ("target", r["target"], close >= (r["target"] or 0) > prev, "reached your target"),
            ("stop", r["stop"], close <= (r["stop"] or 0) < prev, "hit your stop level"),
        ]
        for kind, level, hit, label in checks:
            if level is None or not hit:
                continue
            eid = add_event(
                conn, sid=r["sid"], ts=f"{r['bar_d']}T15:30:00+05:30", kind="note", type=f"note_{kind}_hit",
                severity="high", title=f"Price {label} ₹{level:,.2f}",
                detail=f"Close ₹{close:,.2f} · from note “{r['title']}”: {r['text'][:300]}",
                dedupe=f"notelevel:{r['note_id']}:{r['line_no']}:{r['sid']}:{kind}:{r['bar_d']}",
                data={"note_id": r["note_id"], "level": level, "close": close},
                backfill=bool(live_since is None or r["bar_d"] < live_since),
            )
            n += 1 if eid else 0
    return n
