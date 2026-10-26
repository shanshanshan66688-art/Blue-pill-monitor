#!/bin/bash
# 设置 macOS 定时任务：周一到周五 18:40 自动运行（电脑需开机且未睡眠；睡眠中错过的会在唤醒后补跑）
# 取消：bash install_schedule.sh --remove
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.local.national-team-monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" == "--remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"; echo "已取消定时任务"; exit 0
fi

[ -x "$DIR/.venv/bin/python" ] || { echo "请先运行 bash setup.sh"; exit 1; }
mkdir -p "$DIR/logs" "$HOME/Library/LaunchAgents"

{
cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/python</string><string>$DIR/run_daily.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StandardOutPath</key><string>$DIR/logs/run.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/run.log</string>
  <key>StartCalendarInterval</key>
  <array>
EOF
for wd in 1 2 3 4 5; do
  echo "    <dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>40</integer></dict>"
done
cat <<EOF
  </array>
</dict></plist>
EOF
} > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已设置：工作日 18:40 自动运行。日志：$DIR/logs/run.log  报告：$DIR/reports/latest.html"
echo "立即试跑一次：launchctl start $LABEL"
