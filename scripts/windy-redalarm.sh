#!/usr/bin/env bash
# Mid-week red-alarm: run a fresh v10 scorecard, compare to the
# previous one, and DM the user via Telegram ONLY when something
# has degraded.
#
# Default schedule (set by install-redalarm.sh): Wed + Fri 09:00.
# Designed to be silent on no-change runs — the bot will only ping
# grandma when she actually needs to know.
#
# Usage: bash scripts/windy-redalarm.sh

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
_DEFAULT_AGENT_DIR="/home/grantwhitmer/Desktop/Grant"\'"s Folder/windy-agent"
AGENT_DIR="${WINDY_AGENT_DIR:-$_DEFAULT_AGENT_DIR}"
VENV_PY="${WINDY_VENV_PYTHON:-${AGENT_DIR}/.venv/bin/python}"
HEALTH_DIR="${WINDY_HEALTH_DIR:-/home/grantwhitmer/.windy-stress/health}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    logger -t windy-redalarm \
        "FATAL: TELEGRAM_BOT_TOKEN not set after sourcing $ENV_FILE"
    exit 2
fi

OWNER_ID="${AGENT_OWNER_TELEGRAM_ID:-8545546994}"

# ── Step 1: fresh v10 scorecard (mocked, free) ──
if [[ "${SKIP_HARNESS:-0}" != "1" ]]; then
    cd "$AGENT_DIR" || {
        logger -t windy-redalarm "FATAL: cannot cd to $AGENT_DIR"
        exit 3
    }
    export WINDYFLY_CONFIG="${WINDYFLY_CONFIG:-/home/grantwhitmer/.windy-stress/config.toml}"
    "$VENV_PY" /home/grantwhitmer/.windy-stress/stress_v10_organ_harmony.py \
        > /tmp/windy-redalarm-v10.log 2>&1 || {
        logger -t windy-redalarm \
            "v10 run failed; will compare existing scorecards anyway"
    }
fi

# ── Step 2: ask Python to format the alarm (or empty string) ──
FORMATTER="$(dirname "$(readlink -f "$0")")/windy-redalarm-format.py"
if [[ ! -f "$FORMATTER" ]]; then
    FORMATTER="${HOME}/.local/bin/windy-redalarm-format.py"
fi
export WINDY_HEALTH_DIR="$HEALTH_DIR"
export _AGENT_SRC="${AGENT_DIR}/src"
ALARM="$("$VENV_PY" "$FORMATTER")"

if [[ -z "$ALARM" ]]; then
    logger -t windy-redalarm "no alarm — all clear since previous snapshot"
    exit 0
fi

# ── Step 3: deliver via Telegram ──
HTTP_CODE=$(curl -sS -o /tmp/windy-redalarm-tg.out -w "%{http_code}" \
    --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${OWNER_ID}" \
    --data-urlencode "parse_mode=Markdown" \
    --data-urlencode "text=${ALARM}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    logger -t windy-redalarm \
        "delivery failed: http=$HTTP_CODE body=$(head -c 200 /tmp/windy-redalarm-tg.out 2>/dev/null)"
    exit 1
fi

logger -t windy-redalarm "delivered red-alarm to chat $OWNER_ID"
exit 0
