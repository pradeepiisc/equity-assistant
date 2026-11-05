#!/usr/bin/env bash
# Install / remove the evening LaunchAgents:
#   com.equity-assistant.llm-eval        weekdays 17:00  python -m tracker eval-nightly  (local reads stop at 21:00)
#   com.equity-assistant.priority        daily    21:00  python -m tracker priority      (the priority list)
#   com.equity-assistant.llm-eval-final  weekdays 21:45  python -m tracker eval-final    (once, after 7 evenings)
#
#   bash scripts/evening_schedule.sh install
#   bash scripts/evening_schedule.sh uninstall
#   bash scripts/evening_schedule.sh status
#   bash scripts/evening_schedule.sh logs
#
# Reports: data/tracker/evals/<mon>-<d>-eval.md, final-eval.md and data/tracker/priority/<mon>-<d>-priority.md.
# A slot missed while the Mac sleeps runs on wake (local reads only inside the window). After the comparison week
# the two eval jobs do nothing; remove them with `uninstall` and reinstall, or leave them.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
PY="${TRACKER_PYTHON:-/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python}"
# label:command:days:hh:mm:log  (days: weekdays | daily)
JOBS="com.equity-assistant.llm-eval:eval-nightly:weekdays:17:00:llm_eval
com.equity-assistant.priority:priority:daily:21:00:priority
com.equity-assistant.llm-eval-final:eval-final:weekdays:21:45:llm_eval"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/tracker/logs"
    while IFS=: read -r label cmd days hh mm log; do
      plist="$HOME/Library/LaunchAgents/$label.plist"
      slot() { echo "<dict>${1:+<key>Weekday</key><integer>$1</integer>}<key>Hour</key><integer>$((10#$hh))</integer><key>Minute</key><integer>$((10#$mm))</integer></dict>"; }
      slots=""
      if [ "$days" = weekdays ]; then
        for wd in 1 2 3 4 5; do slots="$slots$(slot "$wd")"; done
      else
        slots="$(slot "")"
      fi
      cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-c</string><string>cd "$ROOT" &amp;&amp; echo "── \$(date '+%Y-%m-%d %H:%M:%S') $cmd ──" &gt;&gt; data/tracker/logs/$log.log &amp;&amp; "$PY" -m tracker $cmd &gt;&gt; data/tracker/logs/$log.log 2&gt;&amp;1</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <array>$slots</array>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$ROOT/data/tracker/logs/$log.err</string>
</dict>
</plist>
EOF
      plutil -lint "$plist" >/dev/null
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      launchctl bootstrap "$DOMAIN" "$plist"
      echo "Installed $label: $days $hh:$mm → python -m tracker $cmd (log data/tracker/logs/$log.log)"
    done <<<"$JOBS"
    ;;
  uninstall)
    while IFS=: read -r label _; do
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      rm -f "$HOME/Library/LaunchAgents/$label.plist"
      echo "Removed $label"
    done <<<"$JOBS"
    ;;
  status)
    while IFS=: read -r label _; do
      echo "$label:"
      if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
        launchctl print "$DOMAIN/$label" | grep -E "state =|last exit code|runs =" || true
      else
        echo "  not installed"
      fi
    done <<<"$JOBS"
    ;;
  logs)
    for f in llm_eval priority; do
      echo "== data/tracker/logs/$f.log"
      tail -n 40 "$ROOT/data/tracker/logs/$f.log" 2>/dev/null || echo "(none yet)"
    done
    ;;
  *)
    echo "usage: $0 install|uninstall|status|logs" >&2
    exit 2
    ;;
esac
