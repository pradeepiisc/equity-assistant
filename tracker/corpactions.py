"""
Split / bonus / consolidation factors, so DMAs aren't broken by corporate actions.

Bhavcopy closes and previous closes are both unadjusted, so ex-dates come from NSE's
daily PR archive (Bc<DDMMYY>.csv lists SYMBOL, EX_DT, PURPOSE such as "BONUS 1:1" or
"FVSPLT FRM RS 10 TO RE 1"):

  https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR<DDMMYY>.zip

Fetched for the latest session every day, and during back-fill only for sessions where
a tracked stock gapped by more than 15%. BSE-only stocks (no such file) fall back to
inferring the factor from overnight gaps no circuit band allows (see `infer`).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from datetime import date, datetime

from .config import RAW_DIR
from .db import kv_get, kv_set
from .util import Http

SCHEMA = """
CREATE TABLE IF NOT EXISTS corp_actions (
    exch TEXT NOT NULL,
    code TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    purpose TEXT NOT NULL,
    factor REAL,
    source TEXT,
    PRIMARY KEY (exch, code, ex_date, purpose)
);
"""

# price multipliers of common splits / bonuses, used to explain gaps on BSE-only stocks
STANDARD = sorted({round(n / (n + m), 4) for m in range(1, 6) for n in range(1, 6)}
                  | {0.5, 0.2, 0.1, 0.05, 0.25, 0.04, 0.02})


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def parse_purpose(purpose: str) -> float | None:
    """Price multiplier implied by a corporate-action purpose, or None if it doesn't change price scale."""
    p = " ".join((purpose or "").upper().split())
    factor = 1.0
    m = re.search(r"BONUS\s*(\d+)\s*:\s*(\d+)", p)
    if m:
        a, b = int(m.group(1)), int(m.group(2))     # a new shares for every b held
        if a and b:
            factor *= b / (a + b)
    m = re.search(r"(?:SPLT|SPLIT|SUB-?DIV\w*).*?(?:RS|RE)\.?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*(?:PER SHARE\s*)?TO\s*(?:RS|RE)\.?\s*(\d+(?:\.\d+)?)", p)
    if m:
        old, new = float(m.group(1)), float(m.group(2))
        if old and new:
            factor *= new / old
    m = re.search(r"CONSOL\w*.*?(?:RS|RE)\.?\s*(\d+(?:\.\d+)?)\s*TO\s*(?:RS|RE)\.?\s*(\d+(?:\.\d+)?)", p)
    if m:
        old, new = float(m.group(1)), float(m.group(2))
        if old and new:
            factor *= new / old
    return None if factor == 1.0 else round(factor, 6)


def _pr_url(d: date) -> str:
    return f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{d.strftime('%d%m%y')}.zip"


def fetch_day(http: Http, d: date) -> list[dict] | None:
    """Bc rows for one session (cached). None when the archive isn't there."""
    cache = RAW_DIR / "corp_actions" / f"{d.strftime('%Y%m%d')}.csv"
    if cache.exists():
        text = cache.read_text()
    else:
        r = http.get(_pr_url(d))
        if r.status_code != 200 or r.content[:2] != b"PK":
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next((n for n in z.namelist() if n.lower().startswith("bc")), None)
            if name is None:
                return []
            text = z.read(name).decode("utf-8", errors="replace")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        ex = _date(r.get("EX_DT", ""))
        if ex is None:
            continue
        rows.append({"code": r.get("SYMBOL", ""), "ex_date": ex, "purpose": r.get("PURPOSE", "")})
    return rows


def _date(s: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def reparse_cache(conn: sqlite3.Connection) -> int:
    """Re-read every cached Bc file (e.g. after a parser fix)."""
    ensure_schema(conn)
    n = 0
    for f in sorted((RAW_DIR / "corp_actions").glob("*.csv")):
        d = date(int(f.stem[:4]), int(f.stem[4:6]), int(f.stem[6:8]))
        n += store(conn, fetch_day(None, d) or [], f"NSE PR {d.isoformat()}")  # type: ignore[arg-type]
    return n


def store(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    n = 0
    for r in rows:
        conn.execute(
            "INSERT OR IGNORE INTO corp_actions(exch, code, ex_date, purpose, factor, source) VALUES('NSE',?,?,?,?,?)",
            (r["code"], r["ex_date"], r["purpose"], parse_purpose(r["purpose"]), source),
        )
        n += 1
    return n


def sync(conn: sqlite3.Connection, http: Http, log=print) -> dict:
    """Latest session's file + official records for unexplained >15% gaps in tracked NSE stocks.

    NSE lists an action a few sessions before its ex-date, so for each unexplained gap the
    gap day and up to five earlier sessions are checked.
    """
    ensure_schema(conn)
    fetched = set(kv_get(conn, "ca_days") or [])
    sessions = [r[0] for r in conn.execute(
        "SELECT d FROM trading_days WHERE exch='NSE' AND rows>0 ORDER BY d")]
    n_rows = n_fetched = 0

    def load(d: str) -> None:
        nonlocal n_rows, n_fetched
        if d in fetched:
            return
        rows = fetch_day(http, date.fromisoformat(d))
        if rows is None:
            return
        n_rows += store(conn, rows, f"NSE PR {d}")
        fetched.add(d)
        n_fetched += 1

    if sessions:
        load(sessions[-1])
    gaps = conn.execute(
        "WITH x AS (SELECT p.code, p.d, p.open, p.close, LAG(p.close) OVER (PARTITION BY p.code ORDER BY p.d) pc "
        "FROM prices p WHERE p.exch='NSE') SELECT code, d FROM x WHERE pc > 0 AND "
        "(abs(open / pc - 1) > 0.15 OR abs(close / pc - 1) > 0.15) ORDER BY d"
    ).fetchall()
    for code, d in gaps:
        idx = sessions.index(d) if d in sessions else None
        if idx is None:
            continue
        for back in range(0, 6):
            if idx - back < 0:
                break
            if _explained(conn, code, d):
                break
            load(sessions[idx - back])
    kv_set(conn, "ca_days", sorted(fetched))
    known = conn.execute("SELECT COUNT(*) FROM corp_actions WHERE factor IS NOT NULL").fetchone()[0]
    return {"sessions_fetched": n_fetched, "rows": n_rows, "price_actions_known": known}


def _explained(conn: sqlite3.Connection, code: str, d: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM corp_actions WHERE exch='NSE' AND code=? AND factor IS NOT NULL AND "
        "abs(julianday(ex_date) - julianday(?)) <= 4", (code, d)).fetchone() is not None


def factors_for(conn: sqlite3.Connection, nse_symbol: str | None) -> list[tuple[str, float, str]]:
    """[(ex_date, factor, purpose)] for one NSE symbol."""
    if not nse_symbol:
        return []
    ensure_schema(conn)
    return [(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT ex_date, factor, purpose FROM corp_actions WHERE exch='NSE' AND code=? AND factor IS NOT NULL "
        "ORDER BY ex_date", (nse_symbol,))]


def infer(prev_close: float, open_: float | None, close: float) -> float | None:
    """Standard split/bonus multiplier that best explains an overnight gap no circuit band allows.

    Candidates must leave the implied day move (close vs adjusted previous close) inside
    the widest 20% band; the smallest implied move wins.
    """
    if not prev_close or not close:
        return None
    move = close / prev_close
    if 0.75 <= move <= 1.33:
        return None                                  # a band move or a small bonus — can't tell
    cands = STANDARD + [round(1 / s, 6) for s in STANDARD]
    best, best_err = None, None
    for s in cands:
        implied = close / (prev_close * s)
        if not 0.8 <= implied <= 1.2:
            continue
        if open_ and not 0.75 <= open_ / (prev_close * s) <= 1.25:
            continue
        err = abs(implied - 1)
        if best_err is None or err < best_err:
            best, best_err = s, err
    return best
