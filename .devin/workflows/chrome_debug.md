---
description: Launch Chrome in remote debug mode (required before any Chrome CDP / browser automation)
---

## Launch Chrome in Debug Mode

Chrome CDP (used for Screener.in scraping, Trendlyne DMA fallback, etc.) requires Chrome to be started with a remote debugging port **before** connecting. A normally-running Chrome instance cannot be used.

**Run this once per session before any browser automation:**

// turbo
```bash
bash /Users/pradeep.bansal1/Documents/learning/equity-assistant/scripts/launch_chrome_debug.sh
```

The script will:
1. Check if Chrome CDP is already running on port 9222 — if yes, exits cleanly
2. Kill any existing Chrome instances (necessary — cannot add debug port retroactively)
3. Relaunch Chrome with `--remote-debugging-port=9222`
4. Wait until the debug session is confirmed ready

After this completes, Chrome CDP tools (in Windsurf or Python) will connect to `http://127.0.0.1:9222`.

---

### Why does Chrome CDP fail with "No browser contexts available"?

**Root cause:** Chrome must be *started* with `--remote-debugging-port=9222` as a launch argument. If Chrome is already running normally (as it usually is), the port is not open and CDP cannot create browser contexts. There is no way to add the debug port to an already-running Chrome process.

**Fix:** Always run this workflow before browser automation. It quits Chrome and relaunches it with the flag.

---

### Verify the session is live

```bash
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool
```

You should see `webSocketDebuggerUrl` in the output.

---

### Critical: MCP server caches the websocket endpoint

The chrome-cdp MCP server reads Chrome's websocket URL **once at startup** (when Windsurf starts) and caches it. If Chrome is relaunched later, the MCP server still tries to connect to the old (now-dead) websocket — resulting in "No browser contexts available" even though Chrome appears to be running with the debug port.

**The only reliable fix: start Chrome in debug mode BEFORE opening Windsurf (or before the MCP server initialises).**

Workaround if Windsurf is already open:
1. Run this workflow (`/chrome_debug`) to launch Chrome with the debug port
2. Restart Windsurf — the MCP server will pick up the new endpoint on re-init
3. Then use Chrome CDP tools normally

If you see `contexts: 0` in the CDP connect response, it means the MCP has a stale cached endpoint → restart Windsurf.

---

### Note on browsing during automation

When Chrome is in debug mode, it behaves normally — you can browse, log in to Screener, etc. The debug port is just an extra channel that allows Windsurf/CDP to send commands. Close Chrome normally when done.
