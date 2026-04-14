#!/bin/bash
# Launch Chrome with remote debugging enabled (port 9222)
# Required before using Chrome CDP tools in Windsurf or any CDP-based automation.
#
# Usage: bash scripts/launch_chrome_debug.sh
#
# After running this, Chrome CDP will connect at: http://127.0.0.1:9222

CHROME_DEBUG_PORT=9222
CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

echo "Checking for existing Chrome debug session on port $CHROME_DEBUG_PORT..."

# Check if something is already listening on the port
if lsof -ti tcp:$CHROME_DEBUG_PORT &>/dev/null; then
    echo "  Port $CHROME_DEBUG_PORT is already in use — Chrome debug session may already be running."
    echo "  Testing connection..."
    if curl -s "http://127.0.0.1:$CHROME_DEBUG_PORT/json/version" | grep -q "webSocketDebuggerUrl"; then
        echo "  ✓ Chrome debug session is active. No need to relaunch."
        exit 0
    else
        echo "  Port busy but not Chrome CDP. Killing the process..."
        lsof -ti tcp:$CHROME_DEBUG_PORT | xargs kill -9 2>/dev/null
        sleep 1
    fi
fi

# Kill any existing Chrome instances (required — you cannot add debug port to a running Chrome)
echo "  Killing existing Chrome instances..."
pkill -x "Google Chrome" 2>/dev/null
sleep 1

echo "  Launching Chrome with --remote-debugging-port=$CHROME_DEBUG_PORT ..."
# Launch Chrome with a PERSISTENT dedicated debug profile stored in home directory.
# This preserves Screener login cookies between restarts — you only need to log in once.
# If you need to reset: rm -rf ~/.config/chrome_screener_debug
CHROME_USER_DATA_DIR="$HOME/.config/chrome_screener_debug"
mkdir -p "$CHROME_USER_DATA_DIR"
nohup "$CHROME_APP" \
    --remote-debugging-port=$CHROME_DEBUG_PORT \
    --remote-allow-origins=* \
    --no-first-run \
    --no-default-browser-check \
    --user-data-dir="$CHROME_USER_DATA_DIR" \
    >/tmp/chrome_debug.log 2>&1 &

# Wait for Chrome to be ready
echo -n "  Waiting for Chrome to start"
for i in {1..30}; do
    sleep 1
    if curl -s "http://127.0.0.1:$CHROME_DEBUG_PORT/json/version" | grep -q "webSocketDebuggerUrl"; then
        echo ""
        echo "  ✓ Chrome debug session ready at http://127.0.0.1:$CHROME_DEBUG_PORT"
        exit 0
    fi
    echo -n "."
done

echo ""
echo "  ✗ Chrome did not start in time. Try running this script again."
exit 1
