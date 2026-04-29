#!/usr/bin/env bash
# Run a fresh v10 organ-harmony scorecard (mocked, free) then DM the
# weekly self-assessment brief to the bot's owner via Telegram.
#
# Used by the systemd timer windy-weekly-brief.timer, but safe to
# run by hand any time. Pure read on the agent — never restarts the
# bot, never blocks the bot's polling loop.
#
# Env (loaded from $WINDY_ENV_FILE):
#   TELEGRAM_BOT_TOKEN          — required
#   AGENT_OWNER_TELEGRAM_ID     — chat to deliver to (defaults to
#                                 Grant's id)
#
# Override knobs:
#   WINDY_ENV_FILE              — env file path
#   WINDY_AGENT_DIR             — windy-agent checkout (CWD for run)
#   WINDY_VENV_PYTHON           — python with windyfly installed
#   WINDY_HEALTH_DIR            — scorecard output dir
#   SKIP_HARNESS=1              — skip running v10, just deliver
#                                 brief from existing scorecards

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
# Default path contains an apostrophe ("Grant's Folder"); use an
# intermediate var so :- expansion doesnt confuse the bash parser.
_DEFAULT_AGENT_DIR="/home/grantwhitmer/Desktop/Grant"\'"s Folder/windy-agent"
AGENT_DIR="${WINDY_AGENT_DIR:-$_DEFAULT_AGENT_DIR}"
VENV_PY="${WINDY_VENV_PYTHON:-${AGENT_DIR}/.venv/bin/python}"
HEALTH_DIR="${WINDY_HEALTH_DIR:-/home/grantwhitmer/.windy-stress/health}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    logger -t windy-weekly-brief \
        "FATAL: TELEGRAM_BOT_TOKEN not set after sourcing $ENV_FILE"
    exit 2
fi

OWNER_ID="${AGENT_OWNER_TELEGRAM_ID:-8545546994}"

# ── Step 1: run a fresh v10 (mocked, free) unless asked to skip ──
if [[ "${SKIP_HARNESS:-0}" != "1" ]]; then
    cd "$AGENT_DIR" || {
        logger -t windy-weekly-brief "FATAL: cannot cd to $AGENT_DIR"
        exit 3
    }
    export WINDYFLY_CONFIG="${WINDYFLY_CONFIG:-/home/grantwhitmer/.windy-stress/config.toml}"
    "$VENV_PY" /home/grantwhitmer/.windy-stress/stress_v10_organ_harmony.py \
        > /tmp/windy-weekly-v10.log 2>&1 || {
        logger -t windy-weekly-brief \
            "v10 run failed; will deliver brief from existing scorecards"
    }
fi

# ── Step 2: format the brief via the sibling Python helper ──
# Kept as a separate .py file rather than an embedded heredoc
# because bash command-substitution + heredoc + Python single quotes
# in dict-key access fight each other regardless of heredoc quoting.
FORMATTER="$(dirname "$(readlink -f "$0")")/windy-weekly-brief-format.py"
if [[ ! -f "$FORMATTER" ]]; then
    # When installed to ~/.local/bin, the sibling .py lives there too.
    FORMATTER="${HOME}/.local/bin/windy-weekly-brief-format.py"
fi
export WINDY_HEALTH_DIR="$HEALTH_DIR"
export _AGENT_SRC="${AGENT_DIR}/src"
BRIEF="$("$VENV_PY" "$FORMATTER")"

if [[ -z "$BRIEF" ]]; then
    logger -t windy-weekly-brief "FATAL: brief formatter produced no output"
    exit 4
fi

# ── Step 3: send via Telegram (--data-urlencode preserves markdown) ──
HTTP_CODE=$(curl -sS -o /tmp/windy-weekly-tg.out -w "%{http_code}" \
    --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${OWNER_ID}" \
    --data-urlencode "parse_mode=Markdown" \
    --data-urlencode "text=${BRIEF}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    logger -t windy-weekly-brief \
        "delivery failed: http=$HTTP_CODE body=$(head -c 200 /tmp/windy-weekly-tg.out 2>/dev/null)"
    exit 1
fi

logger -t windy-weekly-brief "delivered weekly brief to chat $OWNER_ID"
exit 0
