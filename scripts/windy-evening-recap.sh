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
# ── Deliver: Telegram, then email as a second channel (fallback) ──
DELIVER="$(dirname "$(readlink -f "$0")")/windy-deliver.sh"
[[ -f "$DELIVER" ]] || DELIVER="${HOME}/.local/bin/windy-deliver.sh"
# shellcheck disable=SC1090
source "$DELIVER"
windy_deliver windy-evening-recap "${RECAP}" fallback || exit 1
exit 0
