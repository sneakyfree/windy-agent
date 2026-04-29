#!/usr/bin/env bash
# Install the Sunday-evening recap as a systemd user timer.
# Default schedule: Sun 18:00 (9 hours after the morning brief).

set -euo pipefail

WHEN="${WINDY_EVENING_RECAP_WHEN:-Sun 18:00}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SH="${SCRIPT_DIR}/windy-evening-recap.sh"
SOURCE_PY="${SCRIPT_DIR}/windy-evening-recap-format.py"

if [[ ! -x "$SOURCE_SH" ]]; then
    echo "FATAL: $SOURCE_SH not executable" >&2
    exit 1
fi

mkdir -p ~/.local/bin
INSTALLED_PATH="$HOME/.local/bin/windy-evening-recap.sh"
INSTALLED_PY="$HOME/.local/bin/windy-evening-recap-format.py"
install -m 0755 "$SOURCE_SH" "$INSTALLED_PATH"
install -m 0644 "$SOURCE_PY" "$INSTALLED_PY"

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-evening-recap.service <<EOF
[Unit]
Description=Windy Fly Sunday-evening cumulative recap
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-evening-recap.timer <<EOF
[Unit]
Description=Sunday-evening Windy Fly recap (${WHEN})

[Timer]
OnCalendar=${WHEN}
Persistent=true
Unit=windy-evening-recap.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-evening-recap.timer

echo "✓ evening-recap timer installed (${WHEN})"
