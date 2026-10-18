"""
SQLite store for the tracker (data/tracker/tracker.db).

Prices are kept only for tracked stocks; the raw exchange files are cached under
data/tracker/raw so a newly added stock can be back-filled without re-downloading.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH, TRACKER_DIR
from .util import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS refdata (
    exch TEXT NOT NULL,            -- NSE | BSE
    code TEXT NOT NULL,            -- NSE symbol or BSE scrip code
    isin TEXT,
    symbol TEXT,                   -- NSE symbol / BSE ticker
    series TEXT,
    name TEXT,
    norm_name TEXT,
    updated_at TEXT,
    PRIMARY KEY (exch, code)
);
CREATE INDEX IF NOT EXISTS ix_ref_isin ON refdata(isin);
CREATE INDEX IF NOT EXISTS ix_ref_norm ON refdata(norm_name);

CREATE TABLE IF NOT EXISTS stocks (
    sid TEXT PRIMARY KEY,          -- NSE symbol when NSE-listed, else BSE scrip code
    isin TEXT,
    name TEXT,
    sector TEXT,
    nse_symbol TEXT,
    nse_series TEXT,
    bse_code TEXT,
    bse_symbol TEXT,
    price_exch TEXT,               -- exchange whose prices drive the DMAs
    folder TEXT,                   -- data/companies/<folder>, if research exists
    in_portfolio INTEGER DEFAULT 0,
    in_watchlist INTEGER DEFAULT 0,
    in_notes INTEGER DEFAULT 0,
    watch_reason TEXT,
    is_sme INTEGER DEFAULT 0,
    mcap_cr REAL,
    sales_ttm_cr REAL,
    added_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_stocks_isin ON stocks(isin);

CREATE TABLE IF NOT EXISTS holdings (
    account TEXT NOT NULL,
    isin TEXT NOT NULL,
    sid TEXT,
    kite_symbol TEXT,
    exchange TEXT,
    quantity REAL,
    avg_price REAL,
    invested REAL,
    last_price REAL,
    as_of TEXT,
    PRIMARY KEY (account, isin)
);

CREATE TABLE IF NOT EXISTS prices (
    exch TEXT NOT NULL,
    code TEXT NOT NULL,            -- NSE symbol or BSE scrip code
    d TEXT NOT NULL,
    isin TEXT,
    series TEXT,
    open REAL, high REAL, low REAL, close REAL, prev_close REAL,
    volume REAL, turnover REAL,
    PRIMARY KEY (exch, code, d)
);
CREATE INDEX IF NOT EXISTS ix_prices_isin ON prices(isin, d);

CREATE TABLE IF NOT EXISTS trading_days (
    exch TEXT NOT NULL,
    d TEXT NOT NULL,
    rows INTEGER,
    loaded_at TEXT,
    PRIMARY KEY (exch, d)
);

CREATE TABLE IF NOT EXISTS technicals (
    sid TEXT PRIMARY KEY,
    d TEXT,
    close REAL, prev_close REAL, chg_pct REAL,
    dma20 REAL, dma40 REAL, dma50 REAL, dma100 REAL, dma200 REAL,
    hi52 REAL, lo52 REAL,
    volume REAL, vol_avg20 REAL,
    phase TEXT, phase_prev TEXT, phase_since TEXT,
    ret_1w REAL, ret_1m REAL, ret_3m REAL,
    bars INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT,
    ts TEXT NOT NULL,              -- ISO datetime, IST
    d TEXT NOT NULL,
    kind TEXT NOT NULL,            -- technical | filing | deal | sast | pledge | insider | shareholding | note
    type TEXT NOT NULL,
    severity TEXT NOT NULL,        -- info | low | medium | high
    title TEXT,
    detail TEXT,
    value_cr REAL,
    url TEXT,
    source TEXT,
    data TEXT,
    backfill INTEGER DEFAULT 0,
    dedupe TEXT UNIQUE,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_sid ON events(sid, ts);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, type);

CREATE TABLE IF NOT EXISTS feed_items (
    feed TEXT NOT NULL,
    guid TEXT NOT NULL,
    ts TEXT,
    company TEXT,
    isin TEXT,
    sid TEXT,
    subject TEXT,
    description TEXT,
    url TEXT,
    category TEXT,
    fetched_at TEXT,
    PRIMARY KEY (feed, guid)
);
CREATE INDEX IF NOT EXISTS ix_feed_ts ON feed_items(ts);

CREATE TABLE IF NOT EXISTS deals (
    d TEXT NOT NULL,
    exch TEXT NOT NULL,
    kind TEXT NOT NULL,            -- bulk | block
    symbol TEXT NOT NULL,
    security TEXT,
    isin TEXT,
    sid TEXT,
    client TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    value_cr REAL,
    client_type TEXT,              -- ace | fii | dii | prop | other
    investor TEXT,                 -- matched tracked-investor name
    remarks TEXT,
    PRIMARY KEY (d, exch, kind, symbol, client, side, qty, price)
);
CREATE INDEX IF NOT EXISTS ix_deals_sid ON deals(sid, d);

CREATE TABLE IF NOT EXISTS shareholding (
    sid TEXT NOT NULL,
    qdate TEXT NOT NULL,           -- quarter end, YYYY-MM-DD
    quarter TEXT,
    promoters REAL, fiis REAL, diis REAL, public REAL, govt REAL, others REAL,
    shareholders REAL,
    source TEXT,
    PRIMARY KEY (sid, qdate)
);

CREATE TABLE IF NOT EXISTS shareholding_holders (
    sid TEXT NOT NULL,
    qdate TEXT NOT NULL,           -- quarter end, YYYY-MM-DD
    grp TEXT NOT NULL,             -- anchor | fii | dii | promoter | other
    nkey TEXT NOT NULL,            -- normalised holder name
    name TEXT,
    pct REAL,                      -- % of equity
    shares REAL,
    category TEXT,                 -- Screener section / NSE XBRL category
    source TEXT,                   -- nse_xbrl | screener  (nse_xbrl wins for a quarter it covers)
    url TEXT,                      -- NSE filing
    PRIMARY KEY (sid, qdate, grp, nkey)
);
CREATE INDEX IF NOT EXISTS ix_holders_name ON shareholding_holders(nkey);

CREATE TABLE IF NOT EXISTS holder_coverage (   -- quarters a source reported for a group
    sid TEXT NOT NULL,
    qdate TEXT NOT NULL,
    grp TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (sid, qdate, grp)
);

CREATE TABLE IF NOT EXISTS shp_filings (       -- NSE shareholding-pattern XBRL files already read
    url TEXT PRIMARY KEY,
    sid TEXT,
    qdate TEXT,
    holders INTEGER,
    status TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    path TEXT,
    title TEXT,
    source_url TEXT,
    channel TEXT,
    d TEXT,
    body TEXT,
    mtime REAL
);

CREATE TABLE IF NOT EXISTS note_mentions (
    note_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    raw_symbol TEXT NOT NULL,
    sid TEXT,
    stance TEXT,                   -- add | reduce | exit | watch | hold | avoid | mention
    entry REAL,
    target REAL,
    stop REAL,
    text TEXT,
    PRIMARY KEY (note_id, line_no, raw_symbol)
);
CREATE INDEX IF NOT EXISTS ix_mentions_sid ON note_mentions(sid);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER UNIQUE,
    created_at TEXT,
    pushed_at TEXT,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS corp_actions (
    exch TEXT NOT NULL,
    code TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    purpose TEXT NOT NULL,
    factor REAL,
    source TEXT,
    PRIMARY KEY (exch, code, ex_date, purpose)
);

-- ── phase 2: documents, extracted facts, order books ──────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ukey TEXT UNIQUE NOT NULL,     -- source URL, or file:<path> for PDFs already on disk
    sid TEXT,
    kind TEXT,                     -- presentation | transcript | results | order_win | press_release | capacity | acquisition | other
    origin TEXT,                   -- nse | disk | screener
    url TEXT,                      -- exchange link (NSE)
    path TEXT,                     -- local copy, relative to the project root
    sha256 TEXT,
    title TEXT,
    filed_at TEXT,
    period TEXT,                   -- quarter the document talks about, e.g. Q2 FY27
    pages INTEGER,
    chars INTEGER,
    status TEXT,                   -- queued | prepared | irrelevant | no_text | extracted | duplicate | failed
    priority INTEGER DEFAULT 0,    -- higher first; live filings outrank back-fill
    backfill INTEGER DEFAULT 0,
    hits TEXT,                     -- keyword hits per page (json)
    est_tokens INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    model TEXT,
    dup_of INTEGER,
    error TEXT,
    event_id INTEGER,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_sid ON documents(sid, filed_at);
CREATE INDEX IF NOT EXISTS ix_docs_status ON documents(status, priority);
CREATE INDEX IF NOT EXISTS ix_docs_sha ON documents(sha256);

CREATE TABLE IF NOT EXISTS doc_facts (          -- capex, tailwinds, headwinds, policy, rates, inflow, execution
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    sid TEXT,
    section TEXT NOT NULL,
    metric TEXT,
    period TEXT,
    value REAL,
    unit TEXT,
    text TEXT,
    quote TEXT,
    page INTEGER,
    verified REAL,                 -- 1 quote found verbatim · 0.5 near match · 0 not found
    data TEXT
);
CREATE INDEX IF NOT EXISTS ix_facts_sid ON doc_facts(sid, section);

CREATE TABLE IF NOT EXISTS order_book_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    as_of TEXT NOT NULL,           -- date the order book refers to
    value_cr REAL NOT NULL,
    scope TEXT,                    -- total, or a segment
    doc_id INTEGER,
    method TEXT,                   -- llm | insights (earlier AI extraction, quote re-located in the PDF)
    quote TEXT,
    page INTEGER,
    verified REAL,
    url TEXT,
    path TEXT,
    created_at TEXT,
    UNIQUE (sid, as_of, doc_id, scope)
);
CREATE INDEX IF NOT EXISTS ix_obh_sid ON order_book_history(sid, as_of);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    doc_id INTEGER,
    event_id INTEGER,
    announced_on TEXT,
    customer TEXT,
    order_type TEXT,
    value_cr REAL,
    duration_months REAL,
    annual_value_cr REAL,
    description TEXT,
    quote TEXT,
    page INTEGER,
    verified REAL,
    url TEXT,
    method TEXT,                   -- llm | filing_text (NSE announcement text, no LLM)
    dated INTEGER DEFAULT 1,       -- 0 = date is only the document's (e.g. an order list in a presentation)
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_sid ON orders(sid, announced_on);
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_event ON orders(event_id) WHERE method = 'filing_text';

CREATE TABLE IF NOT EXISTS guidance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    doc_id INTEGER,
    stated_on TEXT,
    metric TEXT,                   -- revenue | revenue_growth | ebitda_margin | pat | pat_growth | eps | volume | order_inflow | capex | other
    period TEXT,                   -- FY27, H2 FY27, …
    low REAL,
    high REAL,
    unit TEXT,                     -- cr | pct | x | units
    text TEXT,
    quote TEXT,
    page INTEGER,
    verified REAL,
    url TEXT
);
CREATE INDEX IF NOT EXISTS ix_guid_sid ON guidance(sid, metric, period);

CREATE TABLE IF NOT EXISTS financials_q (       -- NSE results XBRL (no LLM)
    sid TEXT NOT NULL,
    period_end TEXT NOT NULL,
    months INTEGER NOT NULL,       -- 3 = quarter, 6/9/12 = year to date
    basis TEXT NOT NULL,           -- consolidated | standalone
    revenue_cr REAL, other_income_cr REAL, ebitda_cr REAL, finance_cr REAL, da_cr REAL,
    pbt_cr REAL, pat_cr REAL, eps REAL, paid_up_cr REAL, face_value REAL,
    url TEXT,
    filed_at TEXT,
    PRIMARY KEY (sid, period_end, months, basis)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    day TEXT,                      -- IST date the budget is counted against
    doc_id INTEGER,
    sid TEXT,
    model TEXT,
    purpose TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    ms INTEGER,
    ok INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_day ON llm_calls(day);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT,
    started_at TEXT,
    finished_at TEXT,
    ok INTEGER,
    message TEXT
);
"""


MIGRATIONS = [  # columns added after a table first shipped (ALTER is a no-op error when present)
    "ALTER TABLE orders ADD COLUMN dated INTEGER DEFAULT 1",
    "ALTER TABLE guidance ADD COLUMN scope TEXT",          # total | a segment (only total drives estimates)
]


def connect() -> sqlite3.Connection:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    gi = TRACKER_DIR / ".gitignore"
    if not gi.exists():
        gi.write_text("# tracker database and raw exchange files stay local\n*\n")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def kv_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except (TypeError, ValueError):
        return row["v"]


def kv_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO kv(k, v, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (key, json.dumps(value), now_ist().isoformat(timespec="seconds")),
    )


def add_event(
    conn: sqlite3.Connection,
    *,
    sid: str | None,
    ts: str,
    kind: str,
    type: str,
    severity: str,
    title: str,
    dedupe: str,
    detail: str = "",
    value_cr: float | None = None,
    url: str = "",
    source: str = "",
    data: dict | None = None,
    backfill: bool = False,
) -> int | None:
    """Insert an event; returns its id when new, None when it already existed."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO events(sid, ts, d, kind, type, severity, title, detail, value_cr, "
        "url, source, data, backfill, dedupe, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid, ts, ts[:10], kind, type, severity, title, detail, value_cr, url, source,
            json.dumps(data, default=str) if data else None, 1 if backfill else 0, dedupe,
            now_ist().isoformat(timespec="seconds"),
        ),
    )
    return cur.lastrowid if cur.rowcount else None


def record_job(conn: sqlite3.Connection, job: str, started: str, ok: bool, message: str) -> None:
    conn.execute(
        "INSERT INTO job_runs(job, started_at, finished_at, ok, message) VALUES(?,?,?,?,?)",
        (job, started, now_ist().isoformat(timespec="seconds"), 1 if ok else 0, message[:2000]),
    )
    conn.execute("DELETE FROM job_runs WHERE id NOT IN (SELECT id FROM job_runs ORDER BY id DESC LIMIT 500)")
