#!/usr/bin/env bash
# Install the Telegram liveness probe as a systemd user timer.
#
# This is GENERIC — it picks up the agent unit name from $WINDY_AGENT_UNIT
# (default windy-0.service). Running on another instance? Set the env
# var first.
#
# Run: bash scripts/install-liveness-probe.sh

set -euo pipefail

UNIT_NAME="${WINDY_AGENT_UNIT:-windy-0.service}"
INTERVAL="${WINDY_LIVENESS_INTERVAL:-5min}"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/probe-telegram-liveness.sh"

if [[ ! -x "$SCRIPT_PATH" ]]; then
    echo "FATAL: $SCRIPT_PATH is not executable" >&2
    exit 1
fi

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-liveness-probe.service <<EOF
[Unit]
Description=Windy Fly liveness probe — external Telegram getMe check
After=network-online.target

[Service]
Type=oneshot
Environment=WINDY_AGENT_UNIT=${UNIT_NAME}
Environment=WINDY_AGENT_SCOPE=user
ExecStart=${SCRIPT_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-liveness-probe.timer <<EOF
[Unit]
Description=Run Windy Fly liveness probe every ${INTERVAL}

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}
Persistent=true
Unit=windy-liveness-probe.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-liveness-probe.timer

echo "✓ liveness probe installed"
echo "  unit:   windy-liveness-probe.service (oneshot)"
echo "  timer:  windy-liveness-probe.timer (every $INTERVAL)"
echo "  agent:  $UNIT_NAME"
echo
echo "  status:   systemctl --user status windy-liveness-probe.timer"
echo "  next run: systemctl --user list-timers | grep liveness"
echo "  logs:     journalctl --user -u windy-liveness-probe.service -f"
echo "  state:    cat ~/.windy/liveness.status"
