"""
Alerts: new (non-backfill) events at or above `min_inbox_severity` land in the dashboard
inbox; those at or above `min_push_severity` are pushed as macOS notifications.

Scope: stocks in the portfolio, watchlist or notes — plus deals by tracked investors in
stocks you don't follow yet (discovery). Quiet hours hold pushes; more than
`max_push_per_run` pending alerts collapse into one summary notification.
"""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import time as dtime

from .config import SEVERITY_RANK
from .db import kv_get, kv_set
from .util import now_ist


def _in_quiet_hours(quiet: list[str]) -> bool:
    if not quiet or len(quiet) != 2:
        return False
    start, end = (dtime(*map(int, q.split(":"))) for q in quiet)
    now = now_ist().time()
    return (start <= now or now < end) if start > end else (start <= now < end)


def notify_macos(title: str, message: str, subtitle: str = "") -> bool:
    script = [
        "-e", "on run argv",
        "-e", "display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv) "
              'sound name "Glass"',
        "-e", "end run",
    ]
    try:
        subprocess.run(["osascript", *script, message[:240], title[:80], subtitle[:80]],
                       check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _label(e: sqlite3.Row) -> str:
    return e["sid"] or "Discovery"


def process(conn: sqlite3.Connection, settings: dict, push: bool = True) -> dict:
    cfg = settings["alerts"]
    min_inbox = SEVERITY_RANK[cfg["min_inbox_severity"]]
    min_push = SEVERITY_RANK[cfg["min_push_severity"]]
    stamp = now_ist().isoformat(timespec="seconds")

    candidates = conn.execute(
        "SELECT e.*, s.in_portfolio, s.in_watchlist, s.in_notes FROM events e "
        "LEFT JOIN stocks s ON s.sid = e.sid LEFT JOIN alerts a ON a.event_id = e.id "
        "WHERE e.backfill = 0 AND a.id IS NULL ORDER BY e.ts"
    ).fetchall()
    created = 0
    for e in candidates:
        if SEVERITY_RANK.get(e["severity"], 0) < min_inbox:
            continue
        in_scope = e["sid"] is None or e["in_portfolio"] or e["in_watchlist"] or e["in_notes"]
        if not in_scope:
            continue
        conn.execute("INSERT OR IGNORE INTO alerts(event_id, created_at) VALUES(?,?)", (e["id"], stamp))
        created += 1

    # first ever run: fill the inbox but don't push a backlog
    if not kv_get(conn, "alerts_initialized"):
        conn.execute("UPDATE alerts SET pushed_at='initial' WHERE pushed_at IS NULL")
        kv_set(conn, "alerts_initialized", True)
        return {"created": created, "pushed": 0, "note": "initial run — inbox filled, nothing pushed"}

    pending = conn.execute(
        "SELECT a.id AS alert_id, e.* FROM alerts a JOIN events e ON e.id = a.event_id "
        "WHERE a.pushed_at IS NULL ORDER BY e.ts"
    ).fetchall()
    pending = [p for p in pending if SEVERITY_RANK.get(p["severity"], 0) >= min_push]
    if not pending:
        return {"created": created, "pushed": 0}
    if not push or not cfg.get("notify_macos", True) or _in_quiet_hours(cfg.get("quiet_hours") or []):
        return {"created": created, "pushed": 0, "held": len(pending)}

    pushed = 0
    if len(pending) <= cfg["max_push_per_run"]:
        for p in pending:
            if notify_macos(f"{_label(p)} · {p['title']}"[:80], p["detail"] or p["title"],
                            subtitle=f"{p['kind']} · {p['severity']}"):
                pushed += 1
    else:
        lines = [f"{_label(p)}: {p['title']}" for p in pending[:4]]
        more = len(pending) - len(lines)
        if notify_macos(f"{len(pending)} new stock alerts", "\n".join(lines) + (f"\n+{more} more" if more > 0 else ""),
                        subtitle="Open the tracker dashboard for details"):
            pushed = len(pending)
    if pushed:
        ids = [p["alert_id"] for p in pending]
        conn.execute(f"UPDATE alerts SET pushed_at=? WHERE id IN ({','.join('?' * len(ids))})", [stamp, *ids])
    return {"created": created, "pushed": pushed}
