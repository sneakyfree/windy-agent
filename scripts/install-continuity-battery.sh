#!/usr/bin/env bash
# Weekly continuity battery timer (Sun 08:20 — after the fire drill,
# before the 09:00 weekly brief).
set -euo pipefail
AGENT_DIR="${WINDY_AGENT_DIR:-$HOME/.local/share/windyfly/agent}"
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/windy-continuity-battery.service <<UNIT
[Unit]
Description=Windy Fly continuity battery — Principle #7 as a number
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=${AGENT_DIR}
EnvironmentFile=%h/.windy/windy-0.env
ExecStartPre=/bin/rm -rf /tmp/windy-continuity-battery
ExecStartPre=$(command -v uv || echo "$HOME/.local/bin/uv") run python scripts/continuity_battery.py seed
ExecStart=$(command -v uv || echo "$HOME/.local/bin/uv") run python scripts/continuity_battery.py probe
TimeoutStartSec=1200
StandardOutput=journal
StandardError=journal
UNIT
cat > ~/.config/systemd/user/windy-continuity-battery.timer <<UNIT
[Unit]
Description=Weekly continuity battery (Sun 08:20)
[Timer]
OnCalendar=Sun 08:20
Persistent=true
Unit=windy-continuity-battery.service
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now windy-continuity-battery.timer
echo "✓ continuity battery installed (Sun 08:20)"
