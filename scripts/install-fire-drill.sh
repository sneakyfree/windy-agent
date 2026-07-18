#!/usr/bin/env bash
# Install the weekly fire drill as a systemd user timer (Sun 08:00,
# an hour before the weekly brief so the brief can report the result).
set -euo pipefail

SOURCE_PATH="$(cd "$(dirname "$0")" && pwd)/fire-drill.sh"
INSTALLED_PATH="$HOME/.local/bin/windy-fire-drill.sh"
install -m 0755 "$SOURCE_PATH" "$INSTALLED_PATH"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-fire-drill.service <<UNIT
[Unit]
Description=Windy Fly weekly fire drill — rehearse probe-heal, lifeboat, engine-swap, turnover
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
UNIT

cat > ~/.config/systemd/user/windy-fire-drill.timer <<UNIT
[Unit]
Description=Weekly Windy Fly fire drill (Sun 08:00)

[Timer]
OnCalendar=Sun 08:00
Persistent=true
Unit=windy-fire-drill.service

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now windy-fire-drill.timer
echo "✓ fire drill installed (Sun 08:00) — status: cat ~/.windy/fire-drill.status"
