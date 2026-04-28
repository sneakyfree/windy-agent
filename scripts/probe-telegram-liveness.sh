#!/usr/bin/env bash
# Telegram liveness probe — independent of the agent process.
#
# Calls Telegram's getMe API and writes a status record. If the bot
# token is valid AND the network is reachable AND Telegram's API is
# up, the probe returns 0. Anything else returns non-zero — and after
# N consecutive failures, it triggers a service restart.
#
# Why an EXTERNAL probe: the agent's own heartbeat (telegram_bot.py)
# can lie if the polling loop dies silently. systemd's WatchdogSec
# catches that for the LOCAL process, but it can't notice when:
#   - the bot token has been revoked
#   - DNS is broken to api.telegram.org
#   - the bot is rate-limited so hard that all polls 429
# This probe is the ground-truth check from outside the agent.
#
# Run from a systemd timer every 5 minutes. The matching unit files
# live with the per-instance soul repo (this script stays generic).
#
# Env:
#   TELEGRAM_BOT_TOKEN          — required (loaded from $WINDY_ENV_FILE)
#   WINDY_ENV_FILE              — env file path; default ~/.windy/windy-0.env
#   WINDY_LIVENESS_STATUS_FILE  — where to write status; default ~/.windy/liveness.status
#   WINDY_LIVENESS_FAIL_LIMIT   — consecutive failures before restart; default 3
#   WINDY_AGENT_UNIT            — systemd unit to restart on stall; default windy-0.service
#   WINDY_AGENT_SCOPE           — systemctl scope: "user" or "system"; default user

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
STATUS_FILE="${WINDY_LIVENESS_STATUS_FILE:-/home/grantwhitmer/.windy/liveness.status}"
FAIL_LIMIT="${WINDY_LIVENESS_FAIL_LIMIT:-3}"
UNIT="${WINDY_AGENT_UNIT:-windy-0.service}"
SCOPE="${WINDY_AGENT_SCOPE:-user}"

# Load the env file (token lives there, redacted from logs by main.py).
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    echo "FATAL: TELEGRAM_BOT_TOKEN not set after sourcing $ENV_FILE" >&2
    exit 2
fi

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PREV_FAILS=0
if [[ -f "$STATUS_FILE" ]]; then
    PREV_FAILS=$(awk -F= '/^consecutive_fails=/{print $2}' "$STATUS_FILE" 2>/dev/null || echo 0)
    PREV_FAILS=${PREV_FAILS:-0}
fi

URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
HTTP_CODE=$(curl -sS -o /tmp/probe-getme.$$ -w "%{http_code}" \
    --max-time 10 --connect-timeout 5 \
    "$URL" 2>/dev/null || echo "000")
BODY=$(cat /tmp/probe-getme.$$ 2>/dev/null || echo "")
rm -f /tmp/probe-getme.$$

write_status() {
    local result="$1" fails="$2" detail="$3"
    {
        echo "ts=$NOW"
        echo "result=$result"
        echo "consecutive_fails=$fails"
        echo "http_code=$HTTP_CODE"
        echo "detail=$detail"
    } > "$STATUS_FILE"
}

if [[ "$HTTP_CODE" == "200" ]] && [[ "$BODY" == *'"ok":true'* ]]; then
    write_status OK 0 "getMe ok"
    exit 0
fi

NEW_FAILS=$((PREV_FAILS + 1))
write_status FAIL "$NEW_FAILS" "http=$HTTP_CODE body=${BODY:0:120}"

# Don't restart on transient blips — only after the limit.
if (( NEW_FAILS >= FAIL_LIMIT )); then
    SCOPE_FLAG=""
    [[ "$SCOPE" == "user" ]] && SCOPE_FLAG="--user"
    logger -t windy-liveness-probe \
        "Telegram getMe failed ${NEW_FAILS}× in a row — restarting $UNIT"
    systemctl $SCOPE_FLAG restart "$UNIT" || true
    write_status RESTART_TRIGGERED 0 "restarted $UNIT after $NEW_FAILS fails"
fi

exit 1
