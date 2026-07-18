#!/usr/bin/env bash
# Install the daily Journal writer as a systemd user timer (00:30 —
# writes YESTERDAY's dated index over the raw Chronicle).
set -euo pipefail
_DEFAULT_AGENT_DIR="/home/grantwhitmer/Desktop/Grant"\'"s Folder/windy-agent"
AGENT_DIR="${WINDY_AGENT_DIR:-$_DEFAULT_AGENT_DIR}"
INSTALLED="$HOME/.local/bin/windy-write-journal.py"
install -m 0755 "${AGENT_DIR}/scripts/write-journal.py" "$INSTALLED"
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-journal.service <<UNIT
[Unit]
Description=Windy Fly daily Journal — dated index over the Chronicle
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${AGENT_DIR}
EnvironmentFile=%h/.windy/windy-0.env
Environment=_AGENT_SRC=${AGENT_DIR}/src
Environment=WINDYFLY_DB_PATH=%h/.local/share/windyfly/agent/data/windy-0.db
Environment=WINDYFLY_CONFIG=%h/.local/share/windyfly/soul/config.toml
ExecStart=$(command -v uv || echo "$HOME/.local/bin/uv") run python "$INSTALLED"
TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
UNIT

cat > ~/.config/systemd/user/windy-journal.timer <<UNIT
[Unit]
Description=Daily Windy Fly Journal writer (00:30)

[Timer]
OnCalendar=*-*-* 00:30:00
Persistent=true
Unit=windy-journal.service

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now windy-journal.timer
echo "✓ Journal writer installed (daily 00:30) — backfill a day: uv run python scripts/write-journal.py YYYY-MM-DD"
