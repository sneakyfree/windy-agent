#!/usr/bin/env bash
# Install the weekly self-assessment delivery as a systemd user
# timer. Runs every Sunday at 09:00 local time by default.
#
# Same apostrophe-safe install pattern as the liveness probe (#91):
# the script gets copied to ~/.local/bin so the unit's ExecStart
# doesn't have to embed paths containing single quotes.
#
# Run: bash scripts/install-weekly-brief.sh

set -euo pipefail

WHEN="${WINDY_WEEKLY_BRIEF_WHEN:-Sun 09:00}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SH="${SCRIPT_DIR}/windy-weekly-brief.sh"
SOURCE_PY="${SCRIPT_DIR}/windy-weekly-brief-format.py"

if [[ ! -x "$SOURCE_SH" ]]; then
    echo "FATAL: $SOURCE_SH is not executable" >&2
    exit 1
fi

mkdir -p ~/.local/bin
INSTALLED_PATH="$HOME/.local/bin/windy-weekly-brief.sh"
INSTALLED_PY="$HOME/.local/bin/windy-weekly-brief-format.py"
install -m 0755 "$SOURCE_SH" "$INSTALLED_PATH"
install -m 0644 "$SOURCE_PY" "$INSTALLED_PY"

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-weekly-brief.service <<EOF
[Unit]
Description=Windy Fly weekly self-assessment + Telegram delivery
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-weekly-brief.timer <<EOF
[Unit]
Description=Weekly Windy Fly self-assessment (${WHEN})

[Timer]
OnCalendar=${WHEN}
Persistent=true
Unit=windy-weekly-brief.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-weekly-brief.timer

echo "✓ weekly brief timer installed"
echo "  unit:     windy-weekly-brief.service (oneshot)"
echo "  timer:    windy-weekly-brief.timer (${WHEN})"
echo "  script:   $INSTALLED_PATH"
echo
echo "  manual run:   bash $INSTALLED_PATH"
echo "  next fire:    systemctl --user list-timers | grep weekly-brief"
echo "  logs:         journalctl --user -u windy-weekly-brief.service -f"
echo "  recent runs:  systemctl --user status windy-weekly-brief.service --no-pager"
