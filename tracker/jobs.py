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
    from .config import ROOT
    wl = ROOT / "watchlist.yaml"                 # an edited watchlist re-syncs the universe too
    return max(m, wl.stat().st_mtime if wl.exists() else 0.0)


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


def catch_up_job(force: bool = False):
    from . import feeds

    def _cu(conn):
        return feeds.catch_up(conn, _http(), load_settings(), force=force)
    return _run("catch_up", _cu, record=lambda r: "skipped" not in r)


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


def history_job(limit: int = 3):
    from . import financials

    def _h(conn):
        return financials.backfill_history(conn, _http(), limit=limit)
    return _run("history", _h, record=lambda r: bool(r.get("stocks")))


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
            bool(r and ((r.get("extracted") or {}).get("done") or (r.get("extracted_backfill") or {}).get("done"))) or \
            bool(r and (r.get("insights") or {}).get("points")) or \
            bool(r and (r.get("notes_backfill") or {}).get("companies"))
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
    market = now.weekday() <= 5 and _after(now, sch["market_from"]) and not _after(now, sch["market_to"])
    if feed in ("announcements", "bse_announcements") and market:
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
    if _after(now, sch["feeds_from"]) or force:     # also the first tick after the laptop wakes
        res = catch_up_job(force=force)
        if res and "skipped" not in res:
            got = sum((res.get(i) or {}).get("tracked", 0) + (res.get(i) or {}).get("results_tracked", 0)
                      for i in ("equities", "sme"))
            ran.append(f"catch-up:{got}" if "error" not in res else "catch-up:error")

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
    with session() as conn:
        hist_pending = conn.execute(
            "SELECT COUNT(*) FROM stocks WHERE nse_symbol IS NOT NULL AND (in_portfolio=1 OR in_watchlist=1) "
            "AND NOT EXISTS (SELECT 1 FROM kv WHERE k='xbrl_hist:' || sid)").fetchone()[0]
    if hist_pending:
        res = history_job()
        ran.append(f"history:{(res or {}).get('stocks', 0)}")

    alerts_job(push=push)

    # BSE-only fallback and documents last, so neither delays the alerts above; their events alert right after
    if settings["screener_fallback"].get("enabled", True):
        res = bse_job()
        if res and "skipped" not in res:
            ran.append(f"bse:{res.get('sid', '')}")
    if settings["documents"].get("enabled", True):
        res = documents_job()
        if res:
            done = (res.get("extracted") or {}).get("done", 0) + (res.get("extracted_backfill") or {}).get("done", 0)
            nb = (res.get("notes_backfill") or {}).get("companies", 0)
            ran.append("documents" + (f":{done} extracted" if done else "") + (f":{nb} notes" if nb else ""))
    if settings["llm"].get("enabled", True) and settings.get("valuation", {}).get("auto", True):
        res = valuations_job()
        if res and res.get("written"):
            ran.append(f"valuations:{res['written']}")
    # without the model, whenever due: the PIB list every 3 hours in the feed window, the movers scan after prices
    with session() as conn:
        last_pib = kv_get(conn, "policy_polled_at")
    if (force or (_after(now, sch["feeds_from"]) and not _after(now, sch["feeds_to"]))) and \
            (not last_pib or (now - datetime.fromisoformat(last_pib)).total_seconds() >= 60 * policy_every(settings)):
        res = policy_job(fetch=True, match=False)
        if res and res.get("new"):
            ran.append(f"policy:{res['new']} new")
    res = movers_job(explain=False)
    if res and res.get("movers") and res.get("pending") == res.get("movers"):
        ran.append(f"movers:{res['movers']}")
    # the local model (free, a few minutes a call): one task a tick, so it never holds the 3-minute polls for long
    res = local_model_job()
    if res:
        ran.append(res)
    transcripts_ready_check()
    alerts_job(push=push)       # cheap: picks up new events and filings the AI read just settled to medium/high
    return ran


def policy_every(settings: dict) -> int:
    from .policy import EVERY_MIN
    return int(settings.get("policy", {}).get("every_min", EVERY_MIN))


def policy_job(fetch: bool = True, match: bool = True):
    """PIB releases → policy events for the tracked companies they can move (policy.py, local model)."""
    from . import policy

    def _p(conn):
        res = policy.run(conn, _http(), load_settings(), fetch=fetch, max_match=1 if match else 0)
        if fetch and "error" not in res:
            kv_set(conn, "policy_polled_at", now_ist().isoformat(timespec="seconds"))
        return res
    return _run("policy", _p, record=lambda r: bool(r.get("new") or r.get("matched") or r.get("error") or r.get("failed")))


def movers_job(explain: bool = True):
    """The session's unusual price moves among held and watched stocks, with the trigger (movers.py)."""
    from . import movers

    def _m(conn):
        return movers.run(conn, _http(), load_settings(), explain=explain)
    return _run("movers", _m, record=lambda r: bool(r.get("explained") or r.get("error")))


def local_model_job() -> str | None:
    """One local-model task, the most time-sensitive first: the movers' reasons (the day's card), a PIB release's
    companies, a company profile (five a day). The second reads of filings run in the evening only (llm_eval.py),
    and while one is running this waits for the next tick."""
    from . import localllm, movers, policy
    from .llm_eval import LOCAL_LOCK

    settings = load_settings()
    if not settings["llm"].get("enabled", True) or not localllm.available(settings):
        return None
    with tick_lock("tick", LOCAL_LOCK) as got:   # one model in memory at a time
        if not got:
            return None
        with session() as conn:
            movers_left = movers.pending_reasons(conn)
            policy_left = policy.pending(conn)
        if movers_left:
            res = movers_job(explain=True)
            return f"movers:{(res or {}).get('explained', 0)} explained"
        if policy_left:
            res = policy_job(fetch=False, match=True)
            return f"policy:{(res or {}).get('events', 0)} cards" if res else None
        res = profiles_job()
        if res and res.get("profiles"):
            return f"profiles:{sum(1 for p in res['profiles'] if p.get('written'))}"
        return None


def profiles_job():
    """One company profile (what it does, end-markets, exposures) per tick, five a day (profiles.py, local model)."""
    from . import profiles

    def _pr(conn):
        return profiles.run(conn, load_settings(), _http(), limit=1)
    return _run("profiles", _pr, record=lambda r: bool(r.get("profiles")))


def valuations_job(limit: int = 2):
    """Rewrite the valuation memo of companies whose results, notes, order book or guidance changed."""
    from . import valuation

    def _v(conn):
        return valuation.refresh(conn, load_settings(), limit=limit)
    return _run("valuations", _v, record=lambda r: bool(r.get("written") or r.get("failed")))


def transcripts_ready_check():
    """Once a week at most: when no transcript is waiting for its read, say how many could go to the Trash."""
    from . import transcripts
    from .db import add_event

    with session() as conn:
        last = kv_get(conn, "transcripts_ready_at")
        if last and (now_ist() - datetime.fromisoformat(last)).days < 7:
            return
        waiting = conn.execute("SELECT COUNT(*) FROM documents WHERE path LIKE '%/transcripts/%' AND status IN "
                               "('queued','prepared')").fetchone()[0]
        if waiting:
            return
        inv = transcripts.inventory()
        if not inv["read"]:
            return
        stamp = now_ist().isoformat(timespec="seconds")
        add_event(conn, sid=None, ts=stamp, kind="system", type="transcripts_ready", severity="medium",
                  title=f"{len(inv['read'])} old transcripts are read ({inv['read_mb']:.0f} MB) — ready for the Trash",
                  detail="Their figures and quarterly notes are stored. Nothing moves until you run "
                         "`python -m tracker transcripts --trash`, which asks first. Unread ones stay.",
                  source="tracker", dedupe=f"transcripts_ready:{stamp[:10]}")
        kv_set(conn, "transcripts_ready_at", stamp)
        conn.commit()


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
