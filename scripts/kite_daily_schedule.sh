#!/usr/bin/env bash
# Install / remove the macOS LaunchAgent for the daily Kite update (all accounts in tracker.yaml `accounts`).
#
#   bash scripts/kite_daily_schedule.sh install     # weekdays 16:00, then every 30 min until 21:00
#   bash scripts/kite_daily_schedule.sh uninstall
#   bash scripts/kite_daily_schedule.sh status
#   bash scripts/kite_daily_schedule.sh logs
#
# Each run saves today's holdings for every account whose Kite token works and writes its daily report;
# an account that needs signing in gets the Kite login page opened in your browser (you sign in there) and
# is tried again at the next slot. Accounts already saved today are skipped, so later slots cost nothing.
# A slot missed while the Mac sleeps runs once on wake.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.equity-assistant.kite-daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PY="${TRACKER_PYTHON:-/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python}"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/tracker/logs"
    SLOTS=""
    for wd in 1 2 3 4 5; do
      for hm in 16:00 16:30 17:00 17:30 18:00 18:30 19:00 19:30 20:00 20:30 21:00; do
        SLOTS="$SLOTS<dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>${hm%%:*}</integer><key>Minute</key><integer>$((10#${hm##*:}))</integer></dict>"
      done
    done
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-c</string><string>cd "$ROOT" &amp;&amp; echo "── \$(date '+%Y-%m-%d %H:%M:%S') ──" &gt;&gt; data/tracker/logs/kite_daily.log &amp;&amp; "$PY" -m tracker kite-daily &gt;&gt; data/tracker/logs/kite_daily.log 2&gt;&amp;1</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <array>$SLOTS</array>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$ROOT/data/tracker/logs/kite_daily.err</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST" >/dev/null
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed $LABEL — weekdays 16:00–21:00 every 30 min. Log: data/tracker/logs/kite_daily.log"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$LABEL" | grep -E "state =|last exit code|runs =" || true
    else
      echo "Not installed"
    fi
    ;;
  logs)
    tail -n 60 "$ROOT/data/tracker/logs/kite_daily.log"
    ;;
  *)
    echo "usage: $0 install|uninstall|status|logs" >&2
    exit 2
    ;;
esac
