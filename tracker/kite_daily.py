"""
The daily Kite update for every account in `accounts` (tracker.yaml): weekdays at 16:00, then every 30 minutes
until 21:00 (LaunchAgent com.equity-assistant.kite-daily, scripts/kite_daily_schedule.sh), until each account has
saved today's holdings.

Per account:
  • today's access token works → save holdings + positions (portfolio/<acct>/holdings/), write that account's
    daily report (workflows.portfolio_daily_report), and mark the account done for the day;
  • it doesn't → open the Kite login page in the browser (you sign in there; nothing here sees your password) and
    wait up to `wait_s` for Kite's redirect to http://127.0.0.1:8765/; no sign-in → a notification, and the next
    attempt 30 minutes later tries again;
  • no API key / secret for the account in .env → one notification a day saying which keys to add.

The tracker's next tick sees the new holdings: prices, DMAs, phases and the portfolio picture across all
accounts refresh from them, and a stock that left every account is recorded as exited (universe.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime

from .config import ROOT, load_settings
from .db import kv_get, kv_set, session
from .util import now_ist

PY = sys.executable


def _keys(acct: str) -> tuple[str, str]:
    """This account's own Kite app keys — never another account's (ZV3899 also uses the unsuffixed ones)."""
    k = os.getenv(f"KITE_API_KEY_{acct}", "").strip() or (os.getenv("KITE_API_KEY", "").strip() if acct == "ZV3899" else "")
    s = os.getenv(f"KITE_API_SECRET_{acct}", "").strip() or (os.getenv("KITE_API_SECRET", "").strip() if acct == "ZV3899" else "")
    return k, s


def _token_ok(acct: str) -> bool:
    from integrations import kite_connect as kc

    try:
        kc._get_kite_client(acct).profile()
        return True
    except BaseException:          # missing token (it may sys.exit), expired token, network
        return False


def _login(acct: str, wait_s: int) -> bool:
    """Open the login page and wait for the redirect; never prompts on a terminal."""
    import webbrowser

    from integrations import kite_connect as kc
    from kiteconnect import KiteConnect

    key, secret = _keys(acct)
    kite = KiteConnect(api_key=key)
    webbrowser.open(kite.login_url())
    token = kc._catch_request_token_via_server(kc._LOGIN_PORT, timeout=wait_s)
    if not token:
        return False
    data = kite.generate_session(token, api_secret=secret)
    if (data.get("user_id") or "").upper() != acct:
        raise RuntimeError(f"signed in as {data.get('user_id')}, not {acct}")
    kc._write_env_key("KITE_ACCESS_TOKEN", data["access_token"], user_id=acct)
    return True


def _notify(title: str, msg: str) -> None:
    from .alerts import notify_macos
    try:
        notify_macos(title, msg)
    except Exception:
        pass


def _kv(key: str, value=None, read: bool = False):
    """kv read / write that waits out a busy database instead of failing the whole run."""
    import time
    for i in range(6):
        try:
            with session() as conn:
                if read:
                    return kv_get(conn, key)
                kv_set(conn, key, value)
                conn.commit()
                return None
        except Exception as exc:
            if "locked" not in str(exc) or i == 5:
                raise
            time.sleep(10)


def attempt(force: bool = False, wait_s: int = 120) -> dict:
    """One attempt for every account not yet done today. Safe to call any time; cheap when all are done."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)       # tokens written by an earlier attempt or by hand
    from integrations import kite_connect as kc

    settings = load_settings()
    today = now_ist().date().isoformat()
    out: dict = {"at": now_ist().isoformat(timespec="seconds"), "accounts": {}}
    for acct in settings["accounts"]:
        try:
            out["accounts"][acct] = _one(acct, today, force, wait_s, kc)
        except Exception as exc:              # one account's trouble never stops the others
            out["accounts"][acct] = f"error: {exc.__class__.__name__}: {exc}"[:200]
    return out


def _one(acct: str, today: str, force: bool, wait_s: int, kc) -> str:
    st = _kv(f"kite_daily:{acct}", read=True) or {}
    if st.get("day") == today and st.get("status") == "saved" and not force:
        return "done today"
    key, secret = _keys(acct)
    if not key or not secret:
        res = {"status": "no keys", "msg": f"add KITE_API_KEY_{acct} and KITE_API_SECRET_{acct} to .env"}
        if st.get("notified") != today:
            _notify(f"Kite {acct}: not set up", res["msg"])
            res["notified"] = today
        else:
            res["notified"] = today
    else:
        ok = _token_ok(acct)
        if not ok:
            _notify(f"Kite {acct}: please sign in", "The login page is open in your browser (2 minutes); "
                                                     "the next try is in 30 minutes.")
            try:
                ok = _login(acct, wait_s) and _token_ok(acct)
            except Exception as exc:
                res = {"status": "login failed", "msg": f"{exc.__class__.__name__}: {exc}"[:200]}
                ok = None
        if ok:
            try:
                path = kc.save_portfolio(user_id=acct)
                rep = subprocess.run([PY, "-m", "workflows.portfolio_daily_report", "--user", acct],
                                     cwd=ROOT, capture_output=True, text=True, timeout=1200)
                res = {"status": "saved", "path": str(path), "report": "ok" if rep.returncode == 0 else
                       f"report failed: {(rep.stderr or rep.stdout)[-200:]}"}
            except Exception as exc:
                res = {"status": "save failed", "msg": f"{exc.__class__.__name__}: {exc}"[:200]}
        elif ok is False:
            res = {"status": "not signed in", "msg": "no sign-in within the wait; retrying in 30 minutes"}
    res |= {"day": today, "tries": (st.get("tries", 0) + 1) if st.get("day") == today else 1,
            "at": now_ist().isoformat(timespec="seconds")}
    _kv(f"kite_daily:{acct}", res)
    return res["status"]


def status() -> dict:
    with session() as conn:
        return {a: kv_get(conn, f"kite_daily:{a}") for a in load_settings()["accounts"]}


if __name__ == "__main__":
    print(json.dumps(attempt(force="--force" in sys.argv), indent=1))
