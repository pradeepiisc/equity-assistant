"""
Job orchestration used by the CLI, the scheduler tick and the dashboard's sync buttons.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import corpactions, prices, refdata, technicals, universe
from .config import PORTFOLIO_DIR, TICK_LOCK, TRACKER_DIR, USER_AGENT, load_settings
from .db import kv_get, kv_set, record_job, session
from .util import Http, now_ist


def _http() -> Http:
    return Http(USER_AGENT, min_interval=1.0)


def _run(name: str, fn, *args, record=None, **kwargs):
    """Run fn(conn) in a session and log it to job_runs (`record(result)` False → don't log a no-op)."""
    started = now_ist().isoformat(timespec="seconds")
    t0 = time.monotonic()
    print(f"▶ {name}")
    try:
        with session() as conn:
            result = fn(conn, *args, **kwargs)
            if record is None or record(result):
                record_job(conn, name, started, True, str(result)[:2000])
        print(f"✓ {name} ({time.monotonic() - t0:.1f}s) {result if result is not None else ''}")
        return result
    except Exception as exc:
        msg = f"{exc.__class__.__name__}: {exc}"
        print(f"✗ {name}: {msg}")
        traceback.print_exc()
        try:
            with session() as conn:
                record_job(conn, name, started, False, msg)
        except Exception:
            pass
        return False


# ── individual jobs ─────────────────────────────────────────────────────────

def _ensure_bse_refdata(conn, http: Http) -> int:
    if conn.execute("SELECT 1 FROM refdata WHERE exch='BSE' LIMIT 1").fetchone():
        return 0
    d = now_ist().date()
    for _ in range(10):
        if d.weekday() < 5:
            status, rows = prices.fetch_day(http, "BSE", d)
            if rows:
                return refdata.refresh_bse_from_rows(conn, rows)
        d -= timedelta(days=1)
    return 0


def _universe(conn, http: Http | None = None) -> dict:
    settings = load_settings()
    if not conn.execute("SELECT 1 FROM refdata WHERE exch='NSE' LIMIT 1").fetchone():
        http = http or _http()
        refdata.refresh_nse(conn, http)
    if http is not None:
        _ensure_bse_refdata(conn, http)
    from . import notes
    notes.index(conn)
    summary = universe.sync_universe(conn, settings["accounts"])
    # back-fill any stock without price history from the cached bhavcopy files
    # (new holdings, watchlist entries or note mentions — however they were added)
    missing = [r["sid"] for r in conn.execute(
        "SELECT s.sid FROM stocks s WHERE NOT EXISTS (SELECT 1 FROM prices p WHERE p.exch = s.price_exch "
        "AND (p.code = COALESCE(CASE WHEN s.price_exch='NSE' THEN s.nse_symbol ELSE s.bse_code END, '') "
        "OR p.isin = s.isin))")]
    if missing and conn.execute("SELECT 1 FROM trading_days WHERE rows > 0 LIMIT 1").fetchone():
        summary["backfilled"] = missing
        summary["replayed_rows"] = prices.replay(conn, missing)
    kv_set(conn, "universe_synced_at", now_ist().isoformat(timespec="seconds"))
    kv_set(conn, "holdings_mtime", _holdings_mtime())
    return summary


def _holdings_mtime() -> float:
    settings = load_settings()
    m = 0.0
    for acct in settings["accounts"]:
        hf = PORTFOLIO_DIR / acct / "holdings" / "holdings.json"
        if hf.exists():
            m = max(m, hf.stat().st_mtime)
    return m


def init():
    def _init(conn):
        http = _http()
        n = refdata.refresh_nse(conn, http)
        b = _ensure_bse_refdata(conn, http)
        s = _universe(conn, http)
        return {"nse_refdata": n, "bse_refdata": b, **{k: v for k, v in s.items() if k != "unresolved"},
                "unresolved": len(s.get("unresolved", []))}
    return _run("init", _init)


def prices_job(days: int | None = None, until=None):
    def _prices(conn):
        settings = load_settings()
        http = _http()
        summary = prices.sync(conn, http, days or settings["technicals"]["history_days"], until=until)
        summary["corp_actions"] = corpactions.sync(conn, http)
        return summary
    return _run("prices", _prices)


def signals_job():
    def _signals(conn):
        cfg = load_settings()["technicals"]
        live_since = kv_get(conn, "live_since")
        latest = technicals.latest_trading_day(conn)
        first_run = live_since is None
        if first_run and latest:
            # first run: only the latest session's events go to the inbox; older ones are history
            live_since = latest.isoformat()
            kv_set(conn, "live_since", live_since)
            kv_set(conn, "initial_run_until", now_ist().isoformat(timespec="seconds"))
        res = technicals.update_all(conn, cfg, live_since)
        from . import notes
        res["note_levels_hit"] = notes.check_levels(conn, live_since)
        kv_set(conn, "signals_at", now_ist().isoformat(timespec="seconds"))
        return res
    return _run("signals", _signals)


def feeds_job(only: list[str] | None = None):
    from . import feeds

    def _feeds(conn):
        return feeds.poll(conn, _http(), load_settings(), only=only)
    return _run("feeds", _feeds)


def deals_job():
    from . import deals

    def _deals(conn):
        return deals.sync(conn, _http(), load_settings())
    return _run("deals", _deals)


def shareholding_job():
    from . import holders, shareholding

    def _sh(conn):
        res = shareholding.sync(conn, load_settings())
        res["holders"] = holders.sync_screener(conn)
        kv_set(conn, "shareholding_files_sig", shareholding.files_signature())
        return res
    return _run("shareholding", _sh)


def holders_nse_job():
    """Named holders from NSE shareholding-pattern XBRL filings of tracked stocks."""
    from . import holders

    def _h(conn):
        return holders.fetch_nse(conn, _http())
    return _run("holders", _h)


def results_job():
    """Quarterly numbers from NSE results XBRL of tracked stocks (no LLM)."""
    from . import financials

    def _r(conn):
        return financials.fetch(conn, _http())
    return _run("results", _r)


def documents_job(max_seconds: float | None = None, llm: bool | None = None):
    """Filings → PDFs → relevant pages → LLM extraction within the daily budget."""
    from . import documents

    def _d(conn):
        settings = load_settings()
        if llm is not None:
            settings["llm"]["enabled"] = llm
        # its own lock: a CLI `docs` run and the scheduler must never extract the same PDF twice
        with tick_lock("documents", TRACKER_DIR / "docs.lock") as got:
            if not got:
                return {"skipped": "documents already running"}
            return documents.run(conn, _http(), settings, max_seconds=max_seconds)

    def worth_logging(r):
        return bool(r) and any(r.get(k) for k in ("discovered", "discovered_disk", "downloaded", "prepared", "orders_text")) or \
            bool(r and (r.get("extracted") or {}).get("done")) or bool(r and (r.get("insights") or {}).get("points"))
    return _run("documents", _d, record=worth_logging)


def bse_job():
    """BSE-only stocks: one due company via the Screener page in Chrome (gentle; see bse_fallback.py)."""
    from . import bse_fallback

    def _b(conn):
        return bse_fallback.run(conn, load_settings())
    return _run("bse_fallback", _b, record=lambda r: bool(r) and "skipped" not in r)


def _pending_results(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM feed_items f WHERE f.feed='results' AND f.sid IS NOT NULL AND f.url LIKE '%.xml' "
        "AND NOT EXISTS (SELECT 1 FROM kv WHERE k = 'xbrl:' || f.url)").fetchone()[0]


def _pending_shp(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM feed_items f LEFT JOIN shp_filings p ON p.url=f.url WHERE f.feed='shareholding' "
        "AND f.sid IS NOT NULL AND f.url LIKE '%.xml' AND p.url IS NULL").fetchone()[0]


def notes_job():
    def _notes(conn):
        return _universe(conn)
    return _run("notes", _notes)


def alerts_job(push: bool = True):
    from . import alerts

    def _alerts(conn):
        return alerts.process(conn, load_settings(), push=push)
    return _run("alerts", _alerts, record=lambda r: bool(r and (r.get("created") or r.get("pushed") or r.get("held"))))


def universe_job():
    return _run("universe", lambda conn: _universe(conn, _http()))


# ── single-instance lock ────────────────────────────────────────────────────
# The dashboard's poll loop, the LaunchAgent and a manual "Refresh now" may all try to tick;
# an OS file lock (released automatically if the process dies) lets exactly one run.

@contextmanager
def tick_lock(source: str, path=None):
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(path or TICK_LOCK, "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "source": source,
                             "started": now_ist().isoformat(timespec="seconds")}))
        fh.flush()
        try:
            yield True
        finally:
            fh.seek(0)
            fh.truncate()
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def lock_holder() -> dict | None:
    """Who is running a tick right now (None when nobody is). Reads the holder's note; never locks."""
    try:
        info = json.loads(TICK_LOCK.read_text() or "{}")
    except (OSError, ValueError):
        return None
    pid = info.get("pid")
    if not pid:
        return None
    try:
        os.kill(pid, 0)          # still alive? (a crashed holder leaves its note behind)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return info


# ── scheduler tick ──────────────────────────────────────────────────────────

def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _after(now: datetime, hm: str) -> bool:
    h, m = _hm(hm)
    return (now.hour, now.minute) >= (h, m)


def _expected_session(now: datetime, eod_after: str):
    """Latest weekday whose bhavcopy should already be published."""
    d = now.date()
    if not (d.weekday() < 5 and _after(now, eod_after)):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def feed_cadence(now: datetime, feed: str, sch: dict) -> int | None:
    """Minutes between polls of `feed` right now (None = outside the polling window)."""
    if not (_after(now, sch["feeds_from"]) and not _after(now, sch["feeds_to"])):
        return None
    market = now.weekday() < 5 and _after(now, sch["market_from"]) and not _after(now, sch["market_to"])
    if feed == "announcements" and market:
        return int(sch["announcements_every_min"])
    return int(sch["feeds_every_min"])


def due_feeds(conn, now: datetime, sch: dict) -> list[str]:
    from .feeds import FEEDS

    due = []
    for feed in FEEDS:
        every = feed_cadence(now, feed, sch)
        if every is None:
            continue
        last = kv_get(conn, f"feed_polled:{feed}")
        # 15% slack so a tick that lands a few seconds early still polls on schedule
        if not last or (now - datetime.fromisoformat(last)).total_seconds() >= every * 60 * 0.85:
            due.append(feed)
    return due


def tick(push: bool = True, source: str = "cli", force: bool = False):
    """One scheduler pass. Safe to call every minute: each job runs only when it is due.

    `force` polls every feed regardless of cadence (the dashboard's "Refresh now").
    Returns "skipped" when another process is already ticking.
    """
    with tick_lock(source) as got:
        if not got:
            holder = lock_holder() or {}
            print(f"tick skipped — already running ({holder.get('source', '?')} since {holder.get('started', '?')})")
            return "skipped"
        started = now_ist()
        ran: list[str] = []
        ok = True
        try:
            ran = _tick(push, force)
        except Exception as exc:  # keep the history even when a pass dies
            ok = False
            ran.append(f"error: {exc.__class__.__name__}: {exc}")
            traceback.print_exc()
        finally:
            entry = {"source": source, "started": started.isoformat(timespec="seconds"),
                     "finished": now_ist().isoformat(timespec="seconds"), "ok": ok, "ran": ran,
                     "secs": round((now_ist() - started).total_seconds(), 1)}
            try:
                with session() as conn:
                    kv_set(conn, "tick_last", entry)
                    hist = (kv_get(conn, "tick_history") or [])[-79:]
                    kv_set(conn, "tick_history", hist + [entry])
            except Exception:
                pass
        return ok


def _sh_signature() -> str:
    from .shareholding import files_signature
    return files_signature()


def _tick(push: bool, force: bool) -> list[str]:
    from . import notes

    settings = load_settings()
    sch = settings["schedule"]
    now = now_ist()
    weekday = now.weekday() < 5
    ran: list[str] = []

    with session() as conn:
        holdings_changed = _holdings_mtime() != (kv_get(conn, "holdings_mtime") or 0)
        notes_changed = notes.changed(conn)
        last_eod = technicals.latest_trading_day(conn)
        deals_done = kv_get(conn, "deals_done_for")
        sh_done = kv_get(conn, "shareholding_synced_on")
        sh_sig_changed = kv_get(conn, "shareholding_files_sig") != _sh_signature()
        exp = _expected_session(now, sch["eod_after"]).isoformat()
        holiday_checked = conn.execute(
            "SELECT 1 FROM trading_days WHERE d=? AND rows=0 GROUP BY d HAVING COUNT(*)=2", (exp,)).fetchone() is not None
        feeds_due = due_feeds(conn, now, sch)

    if holdings_changed or notes_changed:
        universe_job()
        signals_job()
        ran += ["universe", "signals"]

    if force:
        from .feeds import FEEDS
        feeds_due = list(FEEDS)
    if feeds_due:
        feeds_job(only=feeds_due)
        ran.append("feeds:" + ",".join(feeds_due))

    if weekday and _after(now, sch["deals_after"]) and deals_done != now.date().isoformat():
        deals_job()
        ran.append("deals")

    expected = _expected_session(now, sch["eod_after"])
    if last_eod is None or (last_eod < expected and not holiday_checked):
        res = prices_job(until=expected)
        ran.append("prices")
        if res and (res.get("NSE") or res.get("BSE")):
            signals_job()
            ran.append("signals")

    if sh_done != now.date().isoformat() or sh_sig_changed:
        shareholding_job()
        ran.append("shareholding")

    with session() as conn:
        shp_pending = _pending_shp(conn)
        res_pending = _pending_results(conn)
    if shp_pending:
        holders_nse_job()
        ran.append(f"holders:{shp_pending}")
    if res_pending:
        results_job()
        ran.append(f"results:{res_pending}")

    alerts_job(push=push)

    # BSE-only fallback and documents last, so neither delays the alerts above; their events alert right after
    with session() as conn:
        before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    if settings["screener_fallback"].get("enabled", True):
        res = bse_job()
        if res and "skipped" not in res:
            ran.append(f"bse:{res.get('sid', '')}")
    if settings["documents"].get("enabled", True):
        res = documents_job()
        if res:
            ex = res.get("extracted") or {}
            ran.append(f"documents:{ex.get('done', 0)} extracted" if ex.get("done") else "documents")
    with session() as conn:
        after = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    if after > before:
        alerts_job(push=push)
    return ran


def status():
    with session() as conn:
        def one(q, *a):
            r = conn.execute(q, a).fetchone()
            return r[0] if r else None
        print("Stocks       :", one("SELECT COUNT(*) FROM stocks"),
              "| portfolio", one("SELECT SUM(in_portfolio) FROM stocks"),
              "| watchlist", one("SELECT SUM(in_watchlist) FROM stocks"),
              "| notes", one("SELECT SUM(in_notes) FROM stocks"))
        print("Price days   : NSE", one("SELECT COUNT(*) FROM trading_days WHERE exch='NSE' AND rows>0"),
              "| BSE", one("SELECT COUNT(*) FROM trading_days WHERE exch='BSE' AND rows>0"),
              "| latest", one("SELECT MAX(d) FROM trading_days WHERE rows>0"))
        print("Price rows   :", one("SELECT COUNT(*) FROM prices"))
        print("Live since   :", kv_get(conn, "live_since"))
        for r in conn.execute("SELECT kind, COUNT(*) n, SUM(backfill=0) live FROM events GROUP BY kind"):
            print(f"Events       : {r['kind']:<13} {r['n']:>6}  (live {r['live']})")
        print("Alerts unread:", one("SELECT COUNT(*) FROM alerts WHERE read_at IS NULL"))
        last = kv_get(conn, "tick_last") or {}
        print("Last tick    :", last.get("finished", "never"), f"({last.get('source', '')})" if last else "",
              " ".join(last.get("ran", [])))
        holder = lock_holder()
        print("Tick running :", f"{holder['source']} since {holder['started']}" if holder else "no")
        from .scheduler import launchd_status
        ld = launchd_status()
        print("LaunchAgent  :", ("loaded" if ld["loaded"] else "installed, not loaded") if ld["installed"] else
              "not installed", f"every {ld['interval_min']} min" if ld.get("interval_min") else "")
        print("Recent jobs  :")
        for r in conn.execute("SELECT job, started_at, ok, substr(message,1,90) m FROM job_runs ORDER BY id DESC LIMIT 8"):
            print(f"  {'✓' if r['ok'] else '✗'} {r['started_at']} {r['job']:<12} {r['m']}")
    return True
