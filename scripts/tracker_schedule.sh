#!/usr/bin/env bash
# Install / remove the macOS LaunchAgent that runs the stock tracker every few minutes.
#
#   bash scripts/tracker_schedule.sh install [seconds]   # start polling (default 60 s; runs once now)
#   bash scripts/tracker_schedule.sh uninstall   # stop and remove
#   bash scripts/tracker_schedule.sh status
#   bash scripts/tracker_schedule.sh logs
#
# Each pass runs only what is due (see `schedule:` in tracker.yaml): the announcements feed
# every 1 min on trading days 09:00–18:00, other feeds every 1 min (07:00–23:30),
# bulk/block deals after 17:15 and bhavcopy prices after 18:15 on trading days, then signals
# and alerts. A lock file keeps it from overlapping the dashboard server's own poll loop.
# Missed runs (Mac asleep) catch up on wake. LaunchAgents run in your login session, so
# macOS notifications can be shown.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.equity-assistant.tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-status}" in
  install)
    INTERVAL="${2:-60}"
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/tracker/logs"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/tracker_tick.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$ROOT/data/tracker/logs/launchd.err</string>
</dict>
</plist>
EOF
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed $LABEL — runs every $INTERVAL s. Log: data/tracker/logs/tick.log"
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
    tail -n 60 "$ROOT/data/tracker/logs/tick.log"
    ;;
  *)
    echo "usage: $0 install [seconds]|uninstall|status|logs" >&2
    exit 2
    ;;
esac
