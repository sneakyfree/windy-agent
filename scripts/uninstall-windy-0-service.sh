#!/usr/bin/env bash
# Remove the Windy 0 launchd service (does not touch ~/.windy/windy-0.env).
set -euo pipefail

LABEL="ai.windyfly.windy-0"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ ! -f "$PLIST_PATH" ]; then
    echo "Not installed: $PLIST_PATH"
    exit 0
fi

launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm "$PLIST_PATH"
echo "Removed: $PLIST_PATH"
echo "Note: ~/.windy/windy-0.env was left in place — delete manually if you want to wipe secrets."
