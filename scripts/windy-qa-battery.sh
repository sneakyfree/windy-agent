#!/usr/bin/env bash
# Run the v13 + v14 Q&A stress batteries against the live agent
# stack on Haiku, then DM Grant a one-message summary via Telegram.
# Used by the systemd timer windy-qa-battery.timer; safe to run by
# hand any time.
#
# Pure read on the agent — uses isolated harness DBs, never touches
# production episodes / nodes / cost ledger. Costs ~$0.55 of Haiku
# per run.
#
# Env (loaded from $WINDY_ENV_FILE):
#   TELEGRAM_BOT_TOKEN          — required
#   AGENT_OWNER_TELEGRAM_ID     — chat to deliver to (defaults Grant)
#   ANTHROPIC_API_KEY           — required for the LLM calls
#
# Override knobs:
#   WINDY_ENV_FILE              — env file path
#   WINDY_AGENT_DIR             — windy-agent checkout (CWD for run)
#   WINDY_VENV_PYTHON           — python with windyfly installed
#   WINDY_QA_MODEL              — override model (default Haiku)
#   SKIP_V14=1                  — only run v13 (faster, ~$0.25)
#   SKIP_HARNESS=1              — skip both harnesses; just deliver
#                                 the brief from existing summaries
#                                 (free; validates wiring)

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
# Default path contains an apostrophe ("Grant's Folder"); use an
# intermediate var so :- expansion doesnt confuse the bash parser.
_DEFAULT_AGENT_DIR="/home/grantwhitmer/Desktop/Grant"\'"s Folder/windy-agent"
AGENT_DIR="${WINDY_AGENT_DIR:-$_DEFAULT_AGENT_DIR}"
VENV_PY="${WINDY_VENV_PYTHON:-${AGENT_DIR}/.venv/bin/python}"
MODEL="${WINDY_QA_MODEL:-claude-haiku-4-5-20251001}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    logger -t windy-qa-battery \
        "FATAL: TELEGRAM_BOT_TOKEN not set after sourcing $ENV_FILE"
    exit 2
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    logger -t windy-qa-battery \
        "FATAL: ANTHROPIC_API_KEY not set after sourcing $ENV_FILE"
    exit 2
fi

OWNER_ID="${AGENT_OWNER_TELEGRAM_ID:-8545546994}"

cd "$AGENT_DIR" || {
    logger -t windy-qa-battery "FATAL: cannot cd to $AGENT_DIR"
    exit 3
}

export WINDYFLY_CONFIG="${WINDYFLY_CONFIG:-/home/grantwhitmer/.windy-stress/config.toml}"
export DEFAULT_MODEL="$MODEL"

V13_EXIT=0
V14_EXIT=0

if [[ "${SKIP_HARNESS:-0}" == "1" ]]; then
    logger -t windy-qa-battery "SKIP_HARNESS=1 — using existing summaries"
else
    # ── Run v13 (always) ──
    V13_LOG="/tmp/windy-qa-v13.log"
    "$VENV_PY" /home/grantwhitmer/.windy-stress/stress_v13_qa_battery.py \
        > "$V13_LOG" 2>&1
    V13_EXIT=$?
    logger -t windy-qa-battery "v13 finished exit=$V13_EXIT"

    # ── Run v14 (unless skipped) ──
    V14_LOG="/tmp/windy-qa-v14.log"
    if [[ "${SKIP_V14:-0}" != "1" ]]; then
        "$VENV_PY" /home/grantwhitmer/.windy-stress/stress_v14_extended.py \
            > "$V14_LOG" 2>&1
        V14_EXIT=$?
        logger -t windy-qa-battery "v14 finished exit=$V14_EXIT"
    fi
fi

# ── Format the brief via sibling Python helper ──
FORMATTER="$(dirname "$(readlink -f "$0")")/windy-qa-battery-format.py"
if [[ ! -f "$FORMATTER" ]]; then
    FORMATTER="${HOME}/.local/bin/windy-qa-battery-format.py"
fi

BRIEF="$("$VENV_PY" "$FORMATTER" \
    --v13-exit "$V13_EXIT" \
    --v14-exit "$V14_EXIT" \
    --skip-v14 "${SKIP_V14:-0}" \
)"

if [[ -z "$BRIEF" ]]; then
    logger -t windy-qa-battery "FATAL: formatter produced no output"
    BRIEF="🪰 *Windy QA battery* — formatter failed; check journalctl"
fi

# ── Deliver via Telegram ──
HTTP_CODE=$(curl -sS -o /tmp/windy-qa-tg.out -w "%{http_code}" \
    --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${OWNER_ID}" \
    --data-urlencode "parse_mode=Markdown" \
    --data-urlencode "text=${BRIEF}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    logger -t windy-qa-battery \
        "delivery failed: http=$HTTP_CODE body=$(head -c 200 /tmp/windy-qa-tg.out 2>/dev/null)"
    exit 1
fi

logger -t windy-qa-battery "delivered QA brief to chat $OWNER_ID"
exit 0
