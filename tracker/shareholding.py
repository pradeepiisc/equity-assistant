"""
Quarterly shareholding history from the Screener files the existing fetcher writes
(data/companies/<folder>/shareholding/*_shareholding_*.txt).

Produces:
  • shareholding rows (promoters / FIIs / DIIs / public per quarter)
  • events for promoter or institutional moves beyond the thresholds in tracker.yaml
  • kv 'promoters:<sid>' — promoter names, used to recognise promoters in bulk/block deals

Named holders (anchor / FII / DII / promoter, incl. tracked-investor events) live in holders.py.
"""
from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml

from .config import COMPANIES_DIR, ROOT
from .db import add_event, kv_get, kv_set
from .util import norm_name, now_ist, to_float

CATEGORY_COLS = {"promoters": "promoters", "fiis": "fiis", "diis": "diis", "public": "public",
                 "government": "govt", "others": "others", "no. of shareholders": "shareholders"}
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def quarter_end(label: str) -> str | None:
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label.strip())
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    mon, yr = _MONTHS[m.group(1).lower()], int(m.group(2))
    return date(yr, mon, calendar.monthrange(yr, mon)[1]).isoformat()


def _table(lines: list[str], start: int) -> tuple[list[str], list[list[str]], int]:
    """Markdown table starting at lines[start] → (header, rows, next_index)."""
    header = [c.strip() for c in lines[start].strip().strip("|").split("|")]
    rows, i = [], start + 1
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if not set("".join(cells)) <= set("-: "):
            rows.append(cells)
        i += 1
    return header, rows, i


def parse_file(path: Path) -> dict:
    lines = path.read_text(errors="replace").splitlines()
    out: dict = {"quarters": {}, "holders": {}}
    section = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("## Full Shareholding Table"):
            section = "full"
        elif line.startswith("### "):
            section = line[4:].strip()
        if line.startswith("|") and section:
            header, rows, i = _table(lines, i)
            if section == "full":
                quarters = header[1:]
                for r in rows:
                    key = CATEGORY_COLS.get(r[0].rstrip("+ ").strip().lower())
                    if not key:
                        continue
                    for q, v in zip(quarters, r[1:]):
                        out["quarters"].setdefault(q, {})[key] = to_float(v)
            else:
                qs = header[1:-1] if header and header[-1].lower() == "trend" else header[1:]
                holders = []
                for r in rows:
                    vals = [to_float(v) for v in r[1:1 + len(qs)]]
                    holders.append({"name": r[0].strip(), "values": dict(zip(qs, vals))})
                out["holders"][section] = holders
            section = None if section == "full" else section
            continue
        i += 1
    return out


def _tracked_investors() -> list[str]:
    p = ROOT / "investor_watchlist.yaml"
    if not p.exists():
        return []
    data = (yaml.safe_load(p.read_text()) or {}).get("tracked_investors", {}) or {}
    names: list[str] = []
    for key in ("fiis", "diis", "anchor_public"):
        for n in data.get(key) or []:
            if n and n not in names:
                names.append(str(n))
    return names


_SPELL = {"agrawal": "agarwal", "aggarwal": "agarwal", "agarwala": "agarwal", "krishanlal": "kishanlal",
          "kishenlal": "kishanlal", "pvt": "", "private": "", "limited": "", "ltd": ""}


def name_tokens(name: str) -> set[str]:
    toks = set()
    for t in norm_name(name).split():
        t = _SPELL.get(t, t)
        if len(t) >= 3:
            toks.add(t)
    return toks


def match_investor(client: str, investors: list[str]) -> str | None:
    ct = name_tokens(client)
    for inv in investors:
        it = name_tokens(inv)
        if it and it <= ct:
            return inv
    return None


def files_signature() -> str:
    """Cheap fingerprint of every Screener shareholding file (tick re-syncs when it changes)."""
    newest, count = 0.0, 0
    for p in COMPANIES_DIR.glob("*/shareholding/*_shareholding_*.txt"):
        count += 1
        newest = max(newest, p.stat().st_mtime)
    return f"{count}:{int(newest)}"


def sync(conn: sqlite3.Connection, settings: dict) -> dict:
    th = settings["shareholding"]
    first_run = not kv_get(conn, "shareholding_initialized")
    n_rows = n_events = 0
    for s in conn.execute("SELECT sid, folder FROM stocks WHERE folder IS NOT NULL").fetchall():
        files = sorted((COMPANIES_DIR / s["folder"] / "shareholding").glob("*_shareholding_*.txt"))
        if not files:
            continue
        parsed = parse_file(files[-1])
        sig = f"{files[-1].name}:{files[-1].stat().st_mtime}"
        if kv_get(conn, f"sh_sig:{s['sid']}") == sig:
            continue
        kv_set(conn, f"sh_sig:{s['sid']}", sig)

        series = []
        for q, vals in parsed["quarters"].items():
            qd = quarter_end(q)
            if not qd:
                continue
            conn.execute(
                "INSERT INTO shareholding(sid, qdate, quarter, promoters, fiis, diis, public, govt, others, "
                "shareholders, source) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sid, qdate) DO UPDATE SET "
                "quarter=excluded.quarter, promoters=excluded.promoters, fiis=excluded.fiis, diis=excluded.diis, "
                "public=excluded.public, govt=excluded.govt, others=excluded.others, "
                "shareholders=excluded.shareholders, source=excluded.source",
                (s["sid"], qd, q, vals.get("promoters"), vals.get("fiis"), vals.get("diis"), vals.get("public"),
                 vals.get("govt"), vals.get("others"), vals.get("shareholders"), "screener"),
            )
            series.append((qd, q, vals))
            n_rows += 1
        series.sort()

        for (d0, q0, v0), (d1, q1, v1) in zip(series, series[1:]):
            for key, label, limit in (("promoters", "Promoter", th["promoter_change_pp"]),
                                      ("fiis", "FII", th["institution_change_pp"]),
                                      ("diis", "DII", th["institution_change_pp"])):
                a, b = v0.get(key), v1.get(key)
                if a is None or b is None or abs(b - a) < limit:
                    continue
                up = b > a
                sev = "high" if key == "promoters" else "medium"
                eid = add_event(
                    conn, sid=s["sid"], ts=f"{d1}T18:00:00+05:30", kind="shareholding",
                    type=f"{key}_{'up' if up else 'down'}", severity=sev,
                    title=f"{label} holding {'up' if up else 'down'} {abs(b - a):.2f} pp ({a:.2f}% → {b:.2f}%)",
                    detail=f"{q0} → {q1}", source="Screener shareholding",
                    dedupe=f"sh:{s['sid']}:{key}:{d1}", backfill=first_run,
                    data={"from": a, "to": b, "quarter": q1},
                )
                n_events += 1 if eid else 0

        promoters = [h["name"] for h in parsed["holders"].get("Promoters (Individual)", [])]
        if promoters:
            kv_set(conn, f"promoters:{s['sid']}", promoters)

    kv_set(conn, "shareholding_initialized", True)
    kv_set(conn, "shareholding_synced_on", now_ist().date().isoformat())
    return {"rows": n_rows, "new_events": n_events}
