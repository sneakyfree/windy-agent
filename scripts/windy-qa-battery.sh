#!/usr/bin/env bash
# Run the v13 + v14 Q&A stress batteries as the LAB agent (zero-lab), through Windy Mind, marked synthetic,
# then write a one-message brief to a local file (or a LAB Telegram chat named explicitly).
# Used by the systemd timer windy-qa-battery.timer; safe to run by hand any time.
#
# Pure read on the agent: isolated harness DBs, never production episodes / nodes / cost ledger.
#
# Hub 10-11: synthetic traffic NEVER runs as Grant's real agent (Zero) or on his keys.
# 11-04 (Mind + Hub + Boss): the 11-01 run still minted AS ZERO: it sourced the lab's env but not the lab's state
# folder, so windyfly found Zero's registered key in ~/.windy and used it, plus Grant's own provider keys (29 calls).
# Now the whole run lives in the lab's home (HOME, state folder, env file, key file), and windyfly's lab guard
# refuses to start unless it acts as an ET26-TEST-* passport with its own token and key, with no person's provider
# key or Claude login readable, and a dry-run mint comes back as that same test passport.
#
# Override knobs:
#   WINDY_QA_LAB_HOME           the lab agent's home (default ~/.local/share/zero-lab)
#   WINDY_ENV_FILE              the lab agent's env file (default <lab home>/.windy/agent.env)
#   WINDY_AGENT_DIR             windy-agent checkout to run from (default <lab home>/agent)
#   WINDY_VENV_PYTHON           python with windyfly installed (default <agent dir>/.venv/bin/python)
#   WINDY_QA_MODEL              model (default Haiku)
#   QA_DELIVER=telegram + QA_TELEGRAM_CHAT   a LAB chat only, never Grant's DM
#   SKIP_V14=1 / SKIP_HARNESS=1

set -uo pipefail

REAL_HOME="$HOME"
LAB_HOME="${WINDY_QA_LAB_HOME:-$REAL_HOME/.local/share/zero-lab}"
ENV_FILE="${WINDY_ENV_FILE:-$LAB_HOME/.windy/agent.env}"
AGENT_DIR="${WINDY_AGENT_DIR:-$LAB_HOME/agent}"
VENV_PY="${WINDY_VENV_PYTHON:-${AGENT_DIR}/.venv/bin/python}"
MODEL="${WINDY_QA_MODEL:-claude-haiku-4-5-20251001}"
STRESS_HOME="${WINDY_STRESS_HOME:-$REAL_HOME/.windy-stress}"

if [[ ! -f "$ENV_FILE" ]]; then
    logger -t windy-qa-battery "FATAL: no lab env file at $ENV_FILE"
    exit 2
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# The run lives in the LAB agent's home: windyfly reads its key, state and env from here, never from ~/.windy.
export HOME="$LAB_HOME"
export WINDY_STATE_DIR="$LAB_HOME/.windy"
export WINDY_ENV_FILE="$ENV_FILE"
export WINDY_CREDENTIALS_FILE="$LAB_HOME/.windy/credentials.json"
export WINDY_CLAUDE_CREDENTIALS_PATH="$LAB_HOME/.claude/.credentials.json"
export WINDY_STRESS_HOME="$STRESS_HOME"
# What the lab guard compares against: the REAL agent's files (Zero's on Windy 0).
export WINDY_LAB_GUARD_REAL_ENV="$REAL_HOME/.windy/windy-0.env"
export WINDY_LAB_GUARD_REAL_STATE="$REAL_HOME/.windy"
# No direct provider keys: every model call goes through Mind as the lab agent, never a person's own key.
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_OAUTH_ACCESS_TOKEN OPENAI_API_KEY GROK_API_KEY \
      GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY MISTRAL_API_KEY GROQ_API_KEY OPENROUTER_API_KEY
export WINDY_SYNTHETIC=1

cd "$AGENT_DIR" || {
    logger -t windy-qa-battery "FATAL: cannot cd to $AGENT_DIR"
    exit 3
}

# The guard, with a dry-run mint (no model call): refuse unless this run IS the test agent (names only, no values).
if ! GUARD="$("$VENV_PY" -m windyfly.lab_guard --mint 2>&1)"; then
    logger -t windy-qa-battery "FATAL: lab guard refused: ${GUARD//$'\n'/; }"
    exit 2
fi
logger -t windy-qa-battery "$GUARD"

# Delivery: a local file + the journal by default. Telegram only to a LAB/ops chat named explicitly; never Grant's DM.
QA_DELIVER="${QA_DELIVER:-log}"
OWNER_ID="${QA_TELEGRAM_CHAT:-}"
if [[ "$QA_DELIVER" == "telegram" && ( -z "${TELEGRAM_BOT_TOKEN:-}" || -z "$OWNER_ID" ) ]]; then
    logger -t windy-qa-battery "FATAL: QA_DELIVER=telegram needs TELEGRAM_BOT_TOKEN and QA_TELEGRAM_CHAT (a lab chat)"
    exit 2
fi

# Run a harness with X-Windy-Synthetic installed on every request (refuses if it can't be).
run_synthetic() {
    "$VENV_PY" -c 'import runpy, sys
from windyfly.observability import synthetic
if not synthetic.install():
    sys.exit("X-Windy-Synthetic could not be installed")
path = sys.argv[1]
sys.argv = [path]
runpy.run_path(path, run_name="__main__")' "$1"
}

export WINDYFLY_CONFIG="${WINDYFLY_CONFIG:-$STRESS_HOME/config.toml}"
export DEFAULT_MODEL="$MODEL"

V13_EXIT=0
V14_EXIT=0

if [[ "${SKIP_HARNESS:-0}" == "1" ]]; then
    logger -t windy-qa-battery "SKIP_HARNESS=1 — using existing summaries"
else
    V13_LOG="/tmp/windy-qa-v13.log"
    run_synthetic "$AGENT_DIR/stress/stress_v13_qa_battery.py" > "$V13_LOG" 2>&1
    V13_EXIT=$?
    logger -t windy-qa-battery "v13 finished exit=$V13_EXIT"

    V14_LOG="/tmp/windy-qa-v14.log"
    if [[ "${SKIP_V14:-0}" != "1" ]]; then
        run_synthetic "$AGENT_DIR/stress/stress_v14_extended.py" > "$V14_LOG" 2>&1
        V14_EXIT=$?
        logger -t windy-qa-battery "v14 finished exit=$V14_EXIT"
    fi
fi

FORMATTER="$(dirname "$(readlink -f "$0")")/windy-qa-battery-format.py"
if [[ ! -f "$FORMATTER" ]]; then
    FORMATTER="${REAL_HOME}/.local/bin/windy-qa-battery-format.py"
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

if [[ "$QA_DELIVER" == "telegram" ]]; then
    DELIVER="$(dirname "$(readlink -f "$0")")/windy-deliver.sh"
    [[ -f "$DELIVER" ]] || DELIVER="${REAL_HOME}/.local/bin/windy-deliver.sh"
    # shellcheck disable=SC1090
    source "$DELIVER"
    windy_deliver windy-qa-battery "${BRIEF}" fallback || exit 1
else
    OUT="${STRESS_HOME}/qa-battery-latest.md"
    ( umask 077; printf '%s\n' "$BRIEF" > "$OUT" )
    logger -t windy-qa-battery "brief written to $OUT (lab run, synthetic; not sent to anyone)"
fi
exit 0
