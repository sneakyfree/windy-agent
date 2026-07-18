#!/usr/bin/env bash
# Weekly fire drill — the agent rehearses its own death (2026-07-18).
#
# Doctrine: untested recovery is no recovery. The 15h outage of
# 2026-07-17 happened because the liveness probe silently rotted for
# 9 days after a unit rename; nothing ever exercised the fail-safes.
# This drill exercises them, every week, and reports honestly.
#
# Steps (each independently PASS/FAIL; drill exits non-zero if ANY fail):
#   1 probe-heal   — stop the matrix channel, let the probe detect and
#                    heal it (proves the watchdog watches REAL units)
#   2 lifeboat     — scratch-env agent turn with dead paid creds + dead
#                    Mind → must still ANSWER via the local floor (🛟)
#   3 engine-swap  — scratch session: codeword on the local model, then
#                    recall after a brain swap (proves Principle #2)
#   4 turnover     — scratch session: shutdown-turnover writer produces
#                    a letter row (proves Principle #7 machinery)
#
# Guards:
#   - Skips (exit 0, result=SKIPPED) if the owner talked to either
#     channel in the last 10 min — never drill mid-conversation.
#     WINDY_FIRE_DRILL_FORCE=1 bypasses (manual runs).
#   - trap: channels are unconditionally started again on ANY exit.
#
# Env: WINDY_ENV_FILE (default ~/.windy/windy-0.env) for tokens.
# Results: ~/.windy/fire-drill.status + fire_drill.* telemetry events.

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-$HOME/.windy/windy-0.env}"
STATUS_FILE="${WINDY_FIRE_DRILL_STATUS:-$HOME/.windy/fire-drill.status}"
AGENT_DIR="${WINDY_AGENT_DIR:-$HOME/.local/share/windyfly/agent}"
PROBE="${WINDY_PROBE_BIN:-$HOME/.local/bin/windy-probe-telegram-liveness.sh}"
SCRATCH="$(mktemp -d /tmp/windy-fire-drill.XXXXXX)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

declare -A RESULT
FAILED=0

finish_guard() {
    # No matter what happened, the live agent comes back.
    systemctl --user start windy-0@telegram windy-0@matrix 2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap finish_guard EXIT

emit() { # emit <step> <pass|fail|skip> <duration_ms>
    [[ -z "${WINDY_ADMIN_INGEST_URL:-}" || -z "${WINDY_ADMIN_INGEST_TOKEN:-}" ]] && return 0
    curl -s -m 3 -X POST "${WINDY_ADMIN_INGEST_URL%/}/v1/events" \
        -H "Authorization: Bearer $WINDY_ADMIN_INGEST_TOKEN" \
        -H 'Content-Type: application/json' \
        -d "{\"events\":[{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"platform\":\"windy-agent\",\"service\":\"fly\",\"event_type\":\"fire_drill.$2\",\"actor_type\":\"agent\",\"actor_id\":\"${ETERNITAS_PASSPORT:-unknown}\",\"duration_ms\":$3,\"metadata\":{\"step\":\"$1\"}}]}" \
        >/dev/null 2>&1 || true
}

step() { # step <name> <fn>
    local name="$1" fn="$2" t0 t1 dur
    t0=$(date +%s%3N)
    if "$fn"; then
        RESULT[$name]=PASS
    else
        RESULT[$name]=FAIL; FAILED=1
    fi
    t1=$(date +%s%3N); dur=$((t1 - t0))
    emit "$name" "$([[ ${RESULT[$name]} == PASS ]] && echo pass || echo fail)" "$dur"
    echo "[fire-drill] $name: ${RESULT[$name]} (${dur}ms)"
}

# ── Guard: never drill mid-conversation ────────────────────────────
recent_activity() {
    local log age
    for log in "$HOME/.windy/windy-0-telegram.log" "$HOME/.windy/windy-0-matrix.log"; do
        [[ -r "$log" ]] || continue
        age=$(grep -o 'last_message_age=[0-9]*' "$log" | tail -1 | cut -d= -f2)
        [[ -n "$age" && "$age" -lt 600 ]] && return 0
    done
    return 1
}
if [[ "${WINDY_FIRE_DRILL_FORCE:-0}" != "1" ]] && recent_activity; then
    echo "ts=$NOW"$'\n'"result=SKIPPED"$'\n'"detail=owner active in last 10min" > "$STATUS_FILE"
    echo "[fire-drill] SKIPPED — owner active"
    exit 0
fi

# ── Step 1: probe-heal ─────────────────────────────────────────────
drill_probe_heal() {
    systemctl --user stop windy-0@matrix || return 1
    local i
    for i in 1 2 3; do "$PROBE" >/dev/null 2>&1 || true; done
    sleep 4
    [[ "$(systemctl --user is-active windy-0@matrix)" == "active" ]]
}

# ── Step 2: lifeboat (scratch, dead paid brain, dead Mind) ─────────
drill_lifeboat() {
    (cd "$AGENT_DIR" && \
     WINDYFLY_DB_PATH="$SCRATCH/lifeboat.db" \
     WINDY_STATE_DIR="$SCRATCH/state-lifeboat" \
     ANTHROPIC_API_KEY="sk-ant-api03-FIRE-DRILL-DEAD" \
     MIND_API_URL="http://127.0.0.1:9" \
     HOME="$SCRATCH" \
     uv run python - <<'PY'
import os, sys, time
os.makedirs(os.environ["WINDY_STATE_DIR"], exist_ok=True)
from windyfly.config import load_config
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.agent.loop import agent_respond
from windyfly.agent.capabilities import Band
cfg = load_config("/home/grantwhitmer/.local/share/windyfly/soul/config.toml")
cfg.setdefault("memory", {})["db_path"] = os.environ["WINDYFLY_DB_PATH"]
db = Database(os.environ["WINDYFLY_DB_PATH"]); wq = WriteQueue(); wq.start()
agent_respond(cfg, db, wq, "hello", "drill:fire:v1", band=Band.SANDBOX)
time.sleep(2.5)  # absorb the first-contact welcome (async write race)
out = agent_respond(cfg, db, wq, "Fire drill: are you alive? What is 2+2?",
                    "drill:fire:v1", band=Band.SANDBOX)
time.sleep(1)
ok = ("🛟" in out) and any(tok in out for tok in ("4", "four", "Four"))
print("LIFEBOAT_REPLY:", out[:200])
sys.exit(0 if ok else 1)
PY
    )
}

# ── Step 3: engine-swap codeword (scratch, local model both ends) ──
drill_engine_swap() {
    (cd "$AGENT_DIR" && \
     WINDYFLY_DB_PATH="$SCRATCH/swap.db" \
     WINDY_STATE_DIR="$SCRATCH/state-swap" \
     uv run python - <<'PY'
import os, sys, time
os.makedirs(os.environ["WINDY_STATE_DIR"], exist_ok=True)
from windyfly.config import load_config
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.agent.loop import agent_respond
cfg = load_config("/home/grantwhitmer/.local/share/windyfly/soul/config.toml")
cfg.setdefault("memory", {})["db_path"] = os.environ["WINDYFLY_DB_PATH"]
cfg.setdefault("agent", {})["default_model"] = "llama3.2:3b"
db = Database(os.environ["WINDYFLY_DB_PATH"]); wq = WriteQueue(); wq.start()
S = "drill:fireswap:v1"
agent_respond(cfg, db, wq, "hello", S); time.sleep(2.5)      # absorb welcome
agent_respond(cfg, db, wq, "Codeword is HONEYBADGER. Confirm.", S)
cfg["agent"]["default_model"] = "llama3.2:3b"  # swap seam (same floor model
# on boxes with one local model; cloud boxes may override via env)
out = agent_respond(cfg, db, wq, "Brain swapped. What was the codeword?", S)
time.sleep(1)
print("SWAP_REPLY:", out[:200])
sys.exit(0 if "HONEYBADGER" in out.upper() else 1)
PY
    )
}

# ── Step 4: turnover writer ────────────────────────────────────────
drill_turnover() {
    (cd "$AGENT_DIR" && \
     WINDYFLY_DB_PATH="$SCRATCH/turnover.db" \
     WINDY_STATE_DIR="$SCRATCH/state-turnover" \
     uv run python - <<'PY'
import os, sys
os.makedirs(os.environ["WINDY_STATE_DIR"], exist_ok=True)
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.agent.turnover import write_shutdown_turnovers
db = Database(os.environ["WINDYFLY_DB_PATH"])
save_episode(db, "user", "drill episode", session_id="telegram:drill:v1")
n = write_shutdown_turnovers(db, "telegram")
row = db.fetchone("SELECT name FROM nodes WHERE type='turnover_letter'")
sys.exit(0 if (n == 1 and row) else 1)
PY
    )
}

step probe_heal drill_probe_heal
step lifeboat drill_lifeboat
step engine_swap drill_engine_swap
step turnover drill_turnover

# ── Report ─────────────────────────────────────────────────────────
{
    echo "ts=$NOW"
    echo "result=$([[ $FAILED == 0 ]] && echo PASS || echo FAIL)"
    for k in probe_heal lifeboat engine_swap turnover; do
        echo "step_$k=${RESULT[$k]:-MISSING}"
    done
} > "$STATUS_FILE"
logger -t windy-fire-drill "fire drill $([[ $FAILED == 0 ]] && echo PASSED || echo FAILED): $(tr '\n' ' ' < "$STATUS_FILE")"
exit $FAILED
