"""
CDP Helper
===========
Shared Chrome DevTools Protocol helper for all Screener-hitting skills.

Connects to Chrome's BROWSER-level WebSocket endpoint (from /json/version),
which does NOT require --remote-allow-origins to be set. All page navigation
is done through Target.createTarget + Target.attachToTarget with a flattened
session so commands can be multiplexed on the single browser connection.

IMPORTANT — Screener scraping policy:
  Chrome CDP is the ONLY permitted method for Screener batch operations.
  NEVER fall back to httpx for Screener URLs when running in batch mode.
  Bulk httpx requests will trigger IP bans on Screener's CDN within minutes.

Usage:
    from skills.cdp_helper import is_available, fetch_page_html

    if not is_available():
        raise RuntimeError("Chrome must be running — see scripts/launch_chrome_debug.sh")

    html = fetch_page_html("https://www.screener.in/company/DCAL/")
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx

CDP_URL = "http://127.0.0.1:9222"
PAGE_LOAD_WAIT = 3.5   # seconds to wait after Page.navigate

CHROME_LAUNCH_SCRIPT = Path(__file__).parent.parent / "scripts" / "launch_chrome_debug.sh"
CHROME_START_TIMEOUT = 35   # seconds to wait for Chrome to be ready


def is_available() -> bool:
    """
    Return True if Chrome is running with a CDP debug port on CDP_URL.
    Call this as a pre-flight check before any batch Screener operation.
    """
    try:
        r = httpx.get(f"{CDP_URL}/json/version", timeout=3)
        return r.status_code == 200 and bool(r.json().get("webSocketDebuggerUrl"))
    except Exception:
        return False


def ensure_chrome_running(verbose: bool = True) -> bool:
    """
    Ensure Chrome is running with a CDP debug port.
    If already running, returns True immediately.
    If not, auto-launches Chrome via launch_chrome_debug.sh and waits up to
    CHROME_START_TIMEOUT seconds for it to be ready.

    Returns True if Chrome is (or becomes) available, False if launch failed.
    """
    if is_available():
        return True

    if not CHROME_LAUNCH_SCRIPT.exists():
        if verbose:
            print(f"[cdp] Chrome not running and launch script not found: {CHROME_LAUNCH_SCRIPT}")
            print("[cdp] Start Chrome manually: bash scripts/launch_chrome_debug.sh")
        return False

    if verbose:
        print("[cdp] Chrome CDP not detected — auto-launching Chrome debug session...")

    try:
        subprocess.Popen(
            ["bash", str(CHROME_LAUNCH_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        if verbose:
            print(f"[cdp] Failed to launch Chrome: {exc}")
            print("[cdp] Start manually: bash scripts/launch_chrome_debug.sh")
        return False

    # Poll until ready
    if verbose:
        print(f"[cdp] Waiting up to {CHROME_START_TIMEOUT}s for Chrome to be ready...", end="", flush=True)
    for _ in range(CHROME_START_TIMEOUT):
        time.sleep(1)
        if is_available():
            if verbose:
                print(" ready ✓")
            return True
        if verbose:
            print(".", end="", flush=True)

    if verbose:
        print(" timed out ✗")
        print("[cdp] Chrome did not start in time.")
        print("[cdp] Try manually: bash scripts/launch_chrome_debug.sh  (Terminal.app, NOT Windsurf)")
    return False


def navigate_and_evaluate(
    url: str,
    js_expression: str,
    wait: float = PAGE_LOAD_WAIT,
    ready_check_js: str | None = None,
    ready_check_retries: int = 20,
    ready_check_interval: float = 1.0,
) -> str | None:
    """
    Navigate Chrome to `url`, optionally poll until `ready_check_js` is truthy,
    then evaluate `js_expression` and return the string result.

    Args:
        url:                  URL to navigate to.
        js_expression:        JavaScript to evaluate (must return a JSON-stringified string).
        wait:                 Seconds to wait after navigation before any checks.
        ready_check_js:       Optional JS expression that returns truthy when page is ready.
        ready_check_retries:  Max polls for the ready check.
        ready_check_interval: Seconds between ready-check polls.

    Returns:
        String result of js_expression, or None if Chrome unavailable / error.
    """
    try:
        import websocket  # websocket-client  # noqa: PLC0415
    except ImportError:
        return None

    try:
        ver_resp = httpx.get(f"{CDP_URL}/json/version", timeout=3)
        if ver_resp.status_code != 200:
            return None
        browser_ws_url = ver_resp.json().get("webSocketDebuggerUrl")
        if not browser_ws_url:
            return None

        ws = websocket.create_connection(browser_ws_url, timeout=20)
        cid = [0]

        def _send_browser(method, params=None):
            cid[0] += 1
            msg_id = cid[0]
            ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            for _ in range(500):
                raw = json.loads(ws.recv())
                if raw.get("id") == msg_id:
                    return raw.get("result", {})
            return {}

        def _send_session(session_id, method, params=None):
            cid[0] += 1
            msg_id = cid[0]
            ws.send(json.dumps({
                "id": msg_id, "method": method,
                "params": params or {}, "sessionId": session_id,
            }))
            for _ in range(500):
                raw = json.loads(ws.recv())
                if raw.get("id") == msg_id and raw.get("sessionId") == session_id:
                    return raw.get("result", {})
            return {}

        target_result = _send_browser("Target.createTarget", {"url": "about:blank"})
        target_id = target_result.get("targetId")
        if not target_id:
            ws.close()
            return None

        attach_result = _send_browser("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attach_result.get("sessionId")
        if not session_id:
            ws.close()
            return None

        _send_session(session_id, "Page.enable")
        _send_session(session_id, "Page.navigate", {"url": url})
        time.sleep(wait)

        # Optional ready-check polling
        if ready_check_js:
            for _ in range(ready_check_retries):
                chk = _send_session(session_id, "Runtime.evaluate", {
                    "expression": ready_check_js, "returnByValue": True,
                })
                if chk.get("result", {}).get("value"):
                    break
                time.sleep(ready_check_interval)

        # Main JS evaluation
        eval_result = _send_session(session_id, "Runtime.evaluate", {
            "expression": js_expression, "returnByValue": True,
        })
        value = eval_result.get("result", {}).get("value", "")

        try:
            _send_browser("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass
        ws.close()
        return value if value else None

    except Exception:
        return None


def fetch_page_html(url: str, wait: float = PAGE_LOAD_WAIT) -> str | None:
    """
    Navigate Chrome to `url` via CDP and return the full page outerHTML.
    Returns None if Chrome is not available or an error occurs.

    Connects to the browser-level WebSocket (/json/version →
    webSocketDebuggerUrl) which does NOT require --remote-allow-origins.
    """
    try:
        import websocket  # websocket-client  # noqa: PLC0415
    except ImportError:
        return None

    try:
        # ── Get browser-level WebSocket URL ──────────────────────────────
        ver_resp = httpx.get(f"{CDP_URL}/json/version", timeout=3)
        if ver_resp.status_code != 200:
            return None
        browser_ws_url = ver_resp.json().get("webSocketDebuggerUrl")
        if not browser_ws_url:
            return None

        ws = websocket.create_connection(browser_ws_url, timeout=20)
        cid = [0]

        def _send_browser(method, params=None):
            cid[0] += 1
            msg_id = cid[0]
            ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            for _ in range(500):
                raw = json.loads(ws.recv())
                if raw.get("id") == msg_id:
                    return raw.get("result", {})
            return {}

        def _send_session(session_id, method, params=None):
            cid[0] += 1
            msg_id = cid[0]
            ws.send(json.dumps({
                "id": msg_id,
                "method": method,
                "params": params or {},
                "sessionId": session_id,
            }))
            for _ in range(500):
                raw = json.loads(ws.recv())
                if raw.get("id") == msg_id and raw.get("sessionId") == session_id:
                    return raw.get("result", {})
            return {}

        # ── Create a new tab ──────────────────────────────────────────────
        target_result = _send_browser("Target.createTarget", {"url": "about:blank"})
        target_id = target_result.get("targetId")
        if not target_id:
            ws.close()
            return None

        # ── Attach to the tab (flat session = multiplexed on same WS) ────
        attach_result = _send_browser("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })
        session_id = attach_result.get("sessionId")
        if not session_id:
            ws.close()
            return None

        # ── Enable Page domain ────────────────────────────────────────────
        _send_session(session_id, "Page.enable")

        # ── Navigate ──────────────────────────────────────────────────────
        _send_session(session_id, "Page.navigate", {"url": url})
        time.sleep(wait)

        # ── Extract outerHTML ─────────────────────────────────────────────
        eval_result = _send_session(session_id, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        })
        html = eval_result.get("result", {}).get("value", "")

        # ── Close the tab we created ──────────────────────────────────────
        try:
            _send_browser("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass

        ws.close()
        return html if html else None

    except Exception:
        return None
