#!/usr/bin/env bash
# Install the scorecard purge as a weekly systemd user timer.
# Default fires Sunday 03:00 — runs BEFORE the morning brief so any
# weekly aggregation reads the current set, not the about-to-be-purged.

set -euo pipefail

WHEN="${WINDY_HEALTH_PURGE_WHEN:-Sun 03:00}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SH="${SCRIPT_DIR}/windy-health-purge.sh"

if [[ ! -x "$SOURCE_SH" ]]; then
    echo "FATAL: $SOURCE_SH not executable" >&2
    exit 1
fi

mkdir -p ~/.local/bin
INSTALLED_PATH="$HOME/.local/bin/windy-health-purge.sh"
install -m 0755 "$SOURCE_SH" "$INSTALLED_PATH"

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-health-purge.service <<EOF
[Unit]
Description=Windy Fly weekly purge of old organ scorecards
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-health-purge.timer <<EOF
[Unit]
Description=Weekly Windy Fly health-snapshot purge (${WHEN})

[Timer]
OnCalendar=${WHEN}
Persistent=true
Unit=windy-health-purge.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-health-purge.timer

echo "✓ health-purge timer installed (${WHEN}, retains ${WINDY_HEALTH_RETENTION_DAYS:-90} days)"
