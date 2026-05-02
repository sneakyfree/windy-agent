#!/usr/bin/env bash
# Install the weekly Q&A stress battery as a systemd user timer.
# Runs every Sunday at 09:30 local time by default — 30 min after
# the weekly brief so they don't queue Telegram messages at the
# same instant.
#
# Same apostrophe-safe install pattern as the liveness probe (#91)
# and the weekly brief — the script gets copied to ~/.local/bin so
# the unit's ExecStart doesn't have to embed paths containing
# single quotes.
#
# Run: bash scripts/install-qa-battery-timer.sh

set -euo pipefail

WHEN="${WINDY_QA_BATTERY_WHEN:-Sun 09:30}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SH="${SCRIPT_DIR}/windy-qa-battery.sh"
SOURCE_PY="${SCRIPT_DIR}/windy-qa-battery-format.py"

if [[ ! -f "$SOURCE_SH" ]]; then
    echo "FATAL: $SOURCE_SH not found" >&2
    exit 1
fi
chmod +x "$SOURCE_SH"

mkdir -p ~/.local/bin
INSTALLED_PATH="$HOME/.local/bin/windy-qa-battery.sh"
INSTALLED_PY="$HOME/.local/bin/windy-qa-battery-format.py"
install -m 0755 "$SOURCE_SH" "$INSTALLED_PATH"
install -m 0644 "$SOURCE_PY" "$INSTALLED_PY"

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/windy-qa-battery.service <<EOF
[Unit]
Description=Windy Fly weekly Q&A stress battery + Telegram delivery
After=network-online.target

[Service]
Type=oneshot
ExecStart=${INSTALLED_PATH}
StandardOutput=journal
StandardError=journal
EOF

cat > ~/.config/systemd/user/windy-qa-battery.timer <<EOF
[Unit]
Description=Weekly Windy Fly Q&A stress battery (${WHEN})

[Timer]
OnCalendar=${WHEN}
Persistent=true
Unit=windy-qa-battery.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now windy-qa-battery.timer

echo "✓ QA battery timer installed"
echo "  unit:     windy-qa-battery.service (oneshot)"
echo "  timer:    windy-qa-battery.timer (${WHEN})"
echo "  script:   $INSTALLED_PATH"
echo
echo "  manual run:   bash $INSTALLED_PATH"
echo "  next fire:    systemctl --user list-timers | grep qa-battery"
echo "  logs:         journalctl --user -u windy-qa-battery.service -f"
echo "  recent runs:  systemctl --user status windy-qa-battery.service --no-pager"
echo
echo "  Cost per fire: ~\$0.55 of Haiku (113 prompts)"
echo "  Skip v14:     SKIP_V14=1 bash $INSTALLED_PATH  (~\$0.25)"
