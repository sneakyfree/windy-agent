#!/usr/bin/env bash
# Install Windy 0 as a per-user launchd service.
# - Plist lives at ~/Library/LaunchAgents/ai.windyfly.windy-0.plist
# - Secrets are sourced from ~/.windy/windy-0.env (chmod 600), NOT
#   embedded in the plist — same anti-pattern OpenClaw fell into.
# - launchd KeepAlive restarts the bot on crash/non-zero exit, with a
#   10-second throttle so a true bad-loop doesn't burn the CPU.
# - Logs go to ~/Library/Logs/windy-0.log so they survive /tmp purges.
set -euo pipefail

LABEL="ai.windyfly.windy-0"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
ENV_FILE="$HOME/.windy/windy-0.env"
LOG_FILE="$HOME/Library/Logs/windy-0.log"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="${REPO_DIR}/scripts/run-windy-0.sh"

if [ ! -x "$RUN_SCRIPT" ]; then
    echo "Error: $RUN_SCRIPT missing or not executable." >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    cat <<EOF >&2
Error: $ENV_FILE not found.

Create it before running this script:

    mkdir -p ~/.windy && chmod 700 ~/.windy
    cp ${REPO_DIR}/scripts/windy-0.env.example $ENV_FILE
    chmod 600 $ENV_FILE
    \$EDITOR $ENV_FILE   # paste real values from ACCESS_LOCKBOX

Required keys: ZAI_API_KEY, TELEGRAM_BOT_TOKEN.
EOF
    exit 1
fi

# Lock down the env file if its perms are loose. Bot secrets shouldn't
# be world-readable — same hygiene as ~/.ssh/.
PERMS=$(stat -f "%A" "$ENV_FILE")
if [ "$PERMS" != "600" ]; then
    echo "Tightening $ENV_FILE permissions ($PERMS → 600)" >&2
    chmod 600 "$ENV_FILE"
fi

# Stop any prior instance — launchd one OR a leftover nohup. Two bots
# polling the same Telegram token race for getUpdates and one wins
# silently while the other looks dead.
if launchctl list 2>/dev/null | awk '{print $3}' | grep -qx "$LABEL"; then
    echo "Unloading existing launchd entry..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi
PROC_PATTERN="windyfly.main --channel telegram --config windy-0.toml"
if pgrep -f "$PROC_PATTERN" >/dev/null; then
    echo "Stopping leftover bot processes (SIGTERM, then SIGKILL after 8s)..."
    pkill -TERM -f "$PROC_PATTERN" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8; do
        pgrep -f "$PROC_PATTERN" >/dev/null || break
        sleep 1
    done
    # Force-kill anything still alive — main.py currently holds an
    # asyncio.sleep loop that doesn't observe the channel's graceful
    # shutdown signal, so SIGKILL is the reliable backstop until that
    # is refactored. Tracked as a follow-up.
    pkill -KILL -f "$PROC_PATTERN" 2>/dev/null || true
    sleep 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$(dirname "$LOG_FILE")"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>set -a; source "${ENV_FILE}"; set +a; exec "${RUN_SCRIPT}"</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:${HOME}/.cargo/bin:${HOME}/.local/bin:/usr/bin:/bin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

echo "Installed: $PLIST_PATH"

launchctl load "$PLIST_PATH"
echo "Loaded into launchd."
echo
echo "Logs: $LOG_FILE"
echo
echo "Verify with:"
echo "  launchctl list | grep windy"
echo "  tail -f $LOG_FILE"
echo
echo "Stop:    launchctl unload $PLIST_PATH"
echo "Restart: launchctl unload $PLIST_PATH && launchctl load $PLIST_PATH"
echo "Remove:  ${REPO_DIR}/scripts/uninstall-windy-0-service.sh"
