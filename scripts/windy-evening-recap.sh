#!/usr/bin/env bash
# Sunday-evening cumulative recap. Different angle from the morning
# brief: backward-looking ("what we did together this week"), not
# health-forward.

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
    logger -t windy-evening-recap "FATAL: TELEGRAM_BOT_TOKEN not set"
    exit 2
fi

OWNER_ID="${AGENT_OWNER_TELEGRAM_ID:-8545546994}"

FORMATTER="$(dirname "$(readlink -f "$0")")/windy-evening-recap-format.py"
if [[ ! -f "$FORMATTER" ]]; then
    FORMATTER="${HOME}/.local/bin/windy-evening-recap-format.py"
fi

export WINDY_HEALTH_DIR="$HEALTH_DIR"
export _AGENT_SRC="${AGENT_DIR}/src"
RECAP="$("$VENV_PY" "$FORMATTER")"

if [[ -z "$RECAP" ]]; then
    logger -t windy-evening-recap "no data to recap; staying silent"
    exit 0
fi

HTTP_CODE=$(curl -sS -o /tmp/windy-evening-recap-tg.out -w "%{http_code}" \
    --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${OWNER_ID}" \
    --data-urlencode "parse_mode=Markdown" \
    --data-urlencode "text=${RECAP}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    logger -t windy-evening-recap \
        "delivery failed: http=$HTTP_CODE body=$(head -c 200 /tmp/windy-evening-recap-tg.out 2>/dev/null)"
    exit 1
fi

logger -t windy-evening-recap "delivered evening recap to chat $OWNER_ID"
exit 0
