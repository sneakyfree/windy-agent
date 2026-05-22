#!/bin/bash
# Phase 7.3 — morning triage cron entry point.
# Runs the classifier + appends a day-block to the canary journal.
# Install via the matching systemd timer (canary-morning-triage.timer).
set -u

REPO="/home/grantwhitmer/Desktop/Grant's Folder/windy-agent"
PY="$REPO/.venv/bin/python"
JOURNAL="$HOME/.windy-stress/canary_journal.md"
DATE=$(date +%Y-%m-%d)
DAY=$(( ( $(date +%s) - $(date -d "2026-05-21" +%s) ) / 86400 ))

cd "$REPO" || exit 1

# Run classifier for last 24h
report=$("$PY" "$REPO/scripts/canary-classify.py" --hours 24 2>&1)

# Extract tallies for the digest header
p1=$(echo "$report" | grep -E "P1 \(wake Grant\)" | grep -oE "[0-9]+$" || echo 0)
p2=$(echo "$report" | grep -E "P2 \(morning\)" | grep -oE "[0-9]+$" || echo 0)
p3=$(echo "$report" | grep -E "P3 \(cosmetic\)" | grep -oE "[0-9]+$" || echo 0)

# Severity emoji for the date header
if [ "$p1" -gt 0 ]; then
    badge="🔴 P1 incident — clock RESET"
elif [ "$p2" -gt 5 ]; then
    badge="🟡 P2 backlog elevated"
else
    badge="🟢 clean"
fi

{
    echo ""
    echo "### Day $DAY — $DATE  $badge"
    echo ""
    echo "\`\`\`"
    echo "$report"
    echo "\`\`\`"
    echo ""
} >> "$JOURNAL"

# If P1, also fire a Telegram notification (best-effort; relies on
# the bot's own send-message capability when reachable).
if [ "$p1" -gt 0 ] && [ -x "$REPO/scripts/probe-telegram-liveness.sh" ]; then
    echo "ALERT: $p1 P1 incident(s) in last 24h" | \
        "$REPO/scripts/probe-telegram-liveness.sh" >/dev/null 2>&1 || true
fi

echo "Canary triage written to $JOURNAL (day=$DAY P1=$p1 P2=$p2 P3=$p3)"
