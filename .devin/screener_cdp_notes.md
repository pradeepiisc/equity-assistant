# Screener CDP — Issue History & Recovery Guide
> For AI assistant use only. Updated: 2026-03-06

---

## What Happened (Root Cause)

### Incident: IP Ban from bulk httpx scraping
- The `ensure_company_data` workflow attempted to fetch Screener.in for 138 companies in a batch loop.
- Each company used a **fresh `httpx.Client`** to GET the Screener page directly (no browser session).
- Screener's CDN (Cloudflare) detected the rapid bot-like requests and issued an **IP-level ban**.
- Symptom: SSL EOF / handshake errors from ALL clients — `httpx`, `curl`, Chrome — confirming network-layer block.
- The ban affected the IP address, not just the Python session (even incognito Chrome showed the block).

### Incident: Chrome CDP login not persisting
- `launch_chrome_debug.sh` was using `--user-data-dir="/tmp/chrome_cdp_session"` — a temp dir wiped on reboot.
- Each new Chrome launch started with a fresh profile with **zero cookies / no Screener session**.
- Even though CDP connected fine and pages loaded, Screener served `xx` placeholder values + "Log in to view insights".
- Result: `insights_fetcher` found 0 insight items even though the HTML structure was correct.

---

## Fixes Applied

### 1. Chrome profile made persistent
**File:** `scripts/launch_chrome_debug.sh`
- Changed `CHROME_USER_DATA_DIR` from `/tmp/chrome_cdp_session` to `$HOME/.config/chrome_screener_debug`
- This directory persists between reboots; Screener login cookies survive.
- **First-time setup required:** After running the script once, open the Chrome window, go to screener.in, and log in manually. All future runs will use the saved session.
- To reset (force re-login): `rm -rf ~/.config/chrome_screener_debug`

### 2. httpx fallback removed from insights_fetcher
**File:** `skills/insights_fetcher.py`
- Removed the `httpx` import and fallback code path entirely.
- If Chrome CDP is not available, `run()` returns `status="error"` with a clear message instead of silently falling back to httpx.
- This prevents the scenario where bulk httpx requests cause an IP ban.

### 3. Pre-flight Chrome gate in batch workflow
**File:** `workflows/ensure_company_data.py`
- Added `is_available()` check (from `skills/cdp_helper.py`) BEFORE the batch loop starts.
- If Chrome is not running on port 9222, the workflow aborts immediately with clear instructions.
- Prevents the batch from ever running without Chrome.

### 4. Jittered inter-company delay
**File:** `workflows/ensure_company_data.py`
- Replaced fixed `time.sleep(2.0)` with `random.uniform(8, 13)` seconds between companies.
- Skipped companies (already fetched today) do NOT incur the delay.

### 5. Stop-on-block detection
**File:** `workflows/ensure_company_data.py`
- Added `_looks_like_block(error_str)` which checks for SSL/handshake/connection error signatures.
- If seen mid-batch, the loop breaks immediately rather than hammering the blocked IP.

### 6. `is_available()` added to cdp_helper
**File:** `skills/cdp_helper.py`
- `is_available()` — hits `http://127.0.0.1:9222/json/version` and returns True/False.
- Used as pre-flight check in `ensure_company_data` and `insights_fetcher`.

### 7. `--batch-size N` flag added
**File:** `workflows/ensure_company_data.py`
- Limits companies per run. Use `--batch-size 40` to process in safe chunks rather than 138 at once.

### 8. earnings_season.py switched from httpx to CDP (Apr 2026)
**File:** `workflows/earnings_season.py`
- Removed `_SCREENER_CLIENT` httpx singleton and `_get_screener_client()`.
- All Screener page loads now use `cdp_helper.fetch_page_html()` (real browser tab open/close).
- Pre-flight `ensure_chrome_running()` auto-launches Chrome if not running.
- Jittered delay `random.uniform(3.0, 6.0)` between page loads.
- Stop-on-block detection: aborts after 5 consecutive failures.
- Individual holder AJAX calls still use `httpx.get()` (lightweight JSON, 4 calls per company).

### 9. Python 3.9 compatibility
- All files using `X | None` type annotations got `from __future__ import annotations` added.
- 13 files fixed: `llm/client.py`, `llm/utils.py`, `skills/cdp_helper.py`, `skills/concall_fetcher.py`, `skills/financial_fetcher.py`, `skills/shareholding_fetcher.py`, `skills/valuation_agent.py`, `workflows/ensure_company_data.py`, `workflows/batch_company_analysis.py`, `workflows/full_company_analysis.py`, `workflows/portfolio_daily_report.py`, `workflows/quarterly_review.py`, `integrations/kite_connect.py`.

---

## Chrome CDP Architecture (how it works)

```
Python (skills/cdp_helper.py)
  │
  ├─ GET http://127.0.0.1:9222/json/version
  │    └─ returns { webSocketDebuggerUrl: "ws://..." }
  │
  ├─ WebSocket connect to browser-level WS URL
  │    (browser-level = does NOT need --remote-allow-origins)
  │
  ├─ Target.createTarget { url: "about:blank" }
  │    └─ returns { targetId }
  │
  ├─ Target.attachToTarget { targetId, flatten: true }
  │    └─ returns { sessionId }
  │
  ├─ Page.navigate { url: <screener page> }   (uses saved cookies = logged-in session)
  │
  ├─ Runtime.evaluate { expression: "document.documentElement.outerHTML" }
  │    └─ returns full page HTML with real data (not xx placeholders)
  │
  └─ Target.closeTarget { targetId }
```

Key point: `flatten: true` in `attachToTarget` means all CDP commands use the same WebSocket connection with `sessionId` as a parameter — no separate WS per tab needed.

---

## How to Recover from an IP Ban

1. **Switch network** — use a mobile hotspot or VPN to get a new IP. The ban is on your IP, not your account.
2. **Kill existing Chrome** — `pkill -x "Google Chrome"`
3. **Re-launch Chrome debug session** — from Terminal.app (NOT Windsurf terminal):
   ```bash
   bash scripts/launch_chrome_debug.sh
   ```
4. **Check Chrome opened** — a Chrome window should appear. Navigate to `screener.in` and confirm the page loads.
5. **Log in if needed** — if it's a fresh profile or cookies expired, log in manually.
6. **Run the batch in chunks:**
   ```bash
   python -m workflows.ensure_company_data --insights-only --batch-size 40
   ```
   Wait ~8 minutes, then repeat until all 138 are done (already-fetched ones are skipped automatically).

---

## Why Chrome Must Be Launched from Terminal.app (not Windsurf)

Windsurf's integrated terminal runs inside a sandboxed environment that may not properly propagate the debug Chrome process to the macOS GUI layer. The Chrome binary needs to launch as a full GUI application to bind to port 9222 correctly. Terminal.app launches it in the proper context.

If Chrome debug port is still not binding after launch script runs successfully:
- Check `/tmp/chrome_debug.log` for errors
- Try: `curl http://127.0.0.1:9222/json/version` — should return JSON
- If that fails but the process is running: port 9222 may be blocked by a firewall rule

---

## Screener Insights — What We Can and Cannot Parse

### With login (Chrome has valid Screener session):
- Full numeric table data (capacity utilization %, order book values, etc.)
- Yearly and quarterly tabs both accessible
- Sentiment/category can be inferred from metric names and trends

### Without login (fresh Chrome profile / cookies expired):
- Metric labels/names ARE visible in HTML (e.g. "Coil Products Capacity Utilization (Sangli)")
- All numeric values are replaced with `xx`, `x,xxx` etc. in a blurred overlay `<table aria-hidden="true" class="blur">`
- "Log in to view insights" text present in HTML
- `insights_fetcher` captures the metric labels as insight items (useful as a partial record)

### How to tell if Chrome is actually logged into Screener:
```python
from skills.cdp_helper import fetch_page_html
from bs4 import BeautifulSoup
html = fetch_page_html('https://www.screener.in/company/QPOWER/', wait=5)
soup = BeautifulSoup(html, 'lxml')
login_wall = soup.find(string=lambda t: t and 'Log in to view' in t)
print('Logged in:', login_wall is None)
```

---

## Safe Batch Run — Step by Step

```bash
# 1. From Terminal.app — launch Chrome
bash /path/to/equity-assistant/scripts/launch_chrome_debug.sh

# 2. In the Chrome window that opens — go to screener.in and log in
#    (only needed first time or after cookie expiry)

# 3. From Windsurf terminal — verify login
python -c "
from skills.cdp_helper import fetch_page_html
from bs4 import BeautifulSoup
html = fetch_page_html('https://www.screener.in/company/QPOWER/', wait=5)
soup = BeautifulSoup(html, 'lxml')
print('Logged in:', soup.find(string=lambda t: t and 'Log in to view' in t) is None)
"

# 4. Run in safe batches of 40
python -m workflows.ensure_company_data --insights-only --batch-size 40
# Repeat until all 138 done (skips already-fetched companies automatically)
```
