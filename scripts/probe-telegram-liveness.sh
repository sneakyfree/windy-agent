#!/usr/bin/env bash
# Telegram liveness probe — three-way ground-truth check.
#
# Runs THREE independent checks. ANY one failing increments the
# consecutive-fail counter; FAIL_LIMIT consecutive failures triggers
# `systemctl restart` of the agent unit.
#
#   1. Bot token works:   getMe returns 200 + ok=true
#                         (catches token revoked, DNS, network)
#   2. Bot process alive: systemctl is-active says "active"
#                         (catches "bot was windy-stop'd and never
#                          came back" — the original 2026-04-28
#                          bug where the bot stayed dead 10h while
#                          the probe insisted everything was fine)
#   3. Polling fresh:     last "polling=" heartbeat in the log is
#                         less than HEARTBEAT_MAX_AGE old
#                         (catches polling-loop zombie that the
#                          process still being "active" can't see;
#                          systemd's WatchdogSec also covers this
#                          but the probe is independent)
#
# The probe is deliberately paranoid. Any one of the three failing
# is enough — these conditions should ALWAYS hold for a healthy bot.
#
# Run from a systemd timer every 5 minutes.
#
# Env (all optional):
#   WINDY_ENV_FILE              ~/.windy/windy-0.env
#   WINDY_LIVENESS_STATUS_FILE  ~/.windy/liveness.status
#   WINDY_LIVENESS_FAIL_LIMIT   3
#   WINDY_AGENT_UNITS           windy-0@telegram.service windy-0@matrix.service
#                               (space-separated; one runtime per channel)
#   WINDY_AGENT_SCOPE           user
#   WINDY_AGENT_LOG             ~/.windy/windy-0-telegram.log
#   HEARTBEAT_MAX_AGE_SEC       900 (15 min — 3× the heartbeat interval)

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
STATUS_FILE="${WINDY_LIVENESS_STATUS_FILE:-/home/grantwhitmer/.windy/liveness.status}"
FAIL_LIMIT="${WINDY_LIVENESS_FAIL_LIMIT:-3}"
# Legacy WINDY_AGENT_UNIT still honoured so old installs don't silently
# probe nothing; per-channel split (2026-07-08) means the default is now
# BOTH channel units.
UNITS="${WINDY_AGENT_UNITS:-${WINDY_AGENT_UNIT:-windy-0@telegram.service windy-0@matrix.service}}"
SCOPE="${WINDY_AGENT_SCOPE:-user}"
AGENT_LOG="${WINDY_AGENT_LOG:-/home/grantwhitmer/.windy/windy-0-telegram.log}"
HEARTBEAT_MAX_AGE_SEC="${HEARTBEAT_MAX_AGE_SEC:-900}"

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

write_status() {
    local result="$1" fails="$2" detail="$3"
    {
        echo "ts=$NOW"
        echo "result=$result"
        echo "consecutive_fails=$fails"
        echo "detail=$detail"
    } > "$STATUS_FILE"
}

SCOPE_FLAG=""; [[ "$SCOPE" == "user" ]] && SCOPE_FLAG="--user"

# Restart the given units and VERIFY they came back. Only a verified
# restart resets the consecutive-fail counter — a failed restart (e.g.
# unit renamed/masked, the 2026-07-17 15h outage) must keep counting
# and keep screaming, never report RESTART_TRIGGERED and go quiet.
attempt_restart() {
    local reason="$1"; shift
    local units=("$@") failed=""
    for u in "${units[@]}"; do
        systemctl $SCOPE_FLAG restart "$u" || true
    done
    sleep 3
    for u in "${units[@]}"; do
        if [[ "$(systemctl $SCOPE_FLAG is-active "$u" 2>/dev/null)" != "active" ]]; then
            failed="$failed $u"
        fi
    done
    if [[ -z "$failed" ]]; then
        logger -t windy-liveness-probe "restarted ${units[*]} ($reason) — verified active"
        write_status RESTART_TRIGGERED 0 "restarted ${units[*]} ($reason)"
    else
        logger -t windy-liveness-probe \
            "RESTART FAILED for$failed ($reason) — agent still down, will keep retrying"
        write_status RESTART_FAILED "$NEW_FAILS" "restart failed for$failed ($reason)"
    fi
}

# ── Check 1: bot token + Telegram reachable ────────────────────────

URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
HTTP_CODE=$(curl -sS -o /tmp/probe-getme.$$ -w "%{http_code}" \
    --max-time 10 --connect-timeout 5 \
    "$URL" 2>/dev/null || echo "000")
BODY=$(cat /tmp/probe-getme.$$ 2>/dev/null || echo "")
rm -f /tmp/probe-getme.$$

if [[ "$HTTP_CODE" != "200" ]] || [[ "$BODY" != *'"ok":true'* ]]; then
    NEW_FAILS=$((PREV_FAILS + 1))
    write_status FAIL "$NEW_FAILS" "getMe http=$HTTP_CODE body=${BODY:0:80}"
    if (( NEW_FAILS >= FAIL_LIMIT )); then
        # shellcheck disable=SC2086
        attempt_restart "getMe failed ${NEW_FAILS}x" $UNITS
    fi
    exit 1
fi

# ── Check 2: agent processes are actually active ───────────────────

DEAD_UNITS=()
for u in $UNITS; do
    ACTIVE=$(systemctl $SCOPE_FLAG is-active "$u" 2>/dev/null || echo "missing")
    [[ "$ACTIVE" != "active" ]] && DEAD_UNITS+=("$u:$ACTIVE")
done
if (( ${#DEAD_UNITS[@]} > 0 )); then
    NEW_FAILS=$((PREV_FAILS + 1))
    write_status FAIL "$NEW_FAILS" "unit_state=${DEAD_UNITS[*]} (token works but process is dead)"
    if (( NEW_FAILS >= FAIL_LIMIT )); then
        attempt_restart "units dead ${NEW_FAILS}x" "${DEAD_UNITS[@]%%:*}"
    fi
    exit 1
fi

# ── Check 3: log shows recent polling heartbeat ────────────────────

if [[ -r "$AGENT_LOG" ]]; then
    LAST_HB_LINE=$(grep "polling=" "$AGENT_LOG" 2>/dev/null | tail -1 || true)
    if [[ -n "$LAST_HB_LINE" ]]; then
        # Heartbeat lines start "HH:MM:SS" — combine with file mtime
        # date for a full timestamp. Approximate but plenty for a
        # 15-minute staleness check.
        FILE_MOD_DATE=$(date -r "$AGENT_LOG" +%Y-%m-%d 2>/dev/null || date +%Y-%m-%d)
        HB_TIME=$(echo "$LAST_HB_LINE" | awk '{print $1}')
        HB_EPOCH=$(date -d "$FILE_MOD_DATE $HB_TIME" +%s 2>/dev/null || echo 0)
        NOW_EPOCH=$(date +%s)
        AGE_SEC=$((NOW_EPOCH - HB_EPOCH))
        if (( AGE_SEC > HEARTBEAT_MAX_AGE_SEC )); then
            NEW_FAILS=$((PREV_FAILS + 1))
            write_status FAIL "$NEW_FAILS" \
                "heartbeat stale: ${AGE_SEC}s old (max=${HEARTBEAT_MAX_AGE_SEC}s)"
            if (( NEW_FAILS >= FAIL_LIMIT )); then
                # shellcheck disable=SC2086
                attempt_restart "heartbeat stale ${AGE_SEC}s ${NEW_FAILS}x" $UNITS
            fi
            exit 1
        fi
    fi
fi

# All three checks passed.
write_status OK 0 "getMe ok, units active ($UNITS), heartbeat fresh"
exit 0
