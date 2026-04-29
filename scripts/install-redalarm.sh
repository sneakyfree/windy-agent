#!/usr/bin/env bash
# Install the mid-week red-alarm as a systemd user timer. Default
# schedule fires on Wednesday + Friday at 09:00 local time. Same
# apostrophe-safe install pattern as #91 / #104.

set -euo pipefail

WHEN="${WINDY_REDALARM_WHEN:-Wed,Fri 09:00}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SH="${SCRIPT_DIR}/windy-redalarm.sh"
SOURCE_PY="${SCRIPT_DIR}/windy-redalarm-format.py"

if [[ ! -x "$SOURCE_SH" ]]; then
    echo "FATAL: $SOURCE_SH is not executable" >&2
    exit 1
fi

mkdir -p ~/.local/bin
INSTALLED_PATH="$HOME/.local/bin/windy-redalarm.sh"
INSTALLED_PY="$HOME/.local/bin/windy-redalarm-format.py"
install -m 0755 "$SOURCE_SH" "$INSTALLED_PATH"
install -m 0644 "$SOURCE_PY" "$INSTALLED_PY"

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-redalarm.service <<EOF
[Unit]
Description=Windy Fly mid-week red-alarm (silent unless degraded)
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-redalarm.timer <<EOF
[Unit]
Description=Mid-week Windy Fly red-alarm (${WHEN})

[Timer]
OnCalendar=${WHEN}
Persistent=true
Unit=windy-redalarm.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-redalarm.timer

echo "✓ red-alarm timer installed"
echo "  unit:     windy-redalarm.service (oneshot)"
echo "  timer:    windy-redalarm.timer (${WHEN})"
echo "  script:   $INSTALLED_PATH"
echo
echo "  manual run:   bash $INSTALLED_PATH"
echo "  next fire:    systemctl --user list-timers | grep redalarm"
echo "  logs:         journalctl --user -u windy-redalarm.service -f"
