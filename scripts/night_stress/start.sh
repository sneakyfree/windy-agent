#!/usr/bin/env bash
# Overnight stress harness launcher.
# Created 2026-05-10 — single-paste entry point for the all-night run.
#
# Idempotent: if a run is already in progress, refuses to start a new one
# (you can override via FORCE=1). Always returns within a few seconds —
# the actual harness runs in the background via nohup.

set -euo pipefail

REPO="/home/grantwhitmer/Desktop/Grant's Folder/windy-agent"
STRESS_DIR="$HOME/.windy-stress"
TS=$(date +%Y%m%d_%H%M%S)
RUN_DIR="$STRESS_DIR/run_$TS"

# 1. Stop check
if [[ -f "$STRESS_DIR/STOP" ]]; then
    echo "STOP file present at $STRESS_DIR/STOP — refusing to start."
    echo "Remove it first: rm $STRESS_DIR/STOP"
    exit 1
fi

# 2. Already-running check
if [[ -L "$STRESS_DIR/CURRENT_RUN" ]] && [[ "${FORCE:-0}" != "1" ]]; then
    EXISTING=$(readlink "$STRESS_DIR/CURRENT_RUN")
    if [[ -d "$EXISTING" ]] && [[ -f "$EXISTING/harness.pid" ]]; then
        PID=$(cat "$EXISTING/harness.pid")
        if kill -0 "$PID" 2>/dev/null; then
            echo "A run is already active:"
            echo "  dir: $EXISTING"
            echo "  pid: $PID"
            echo "Use FORCE=1 to start anyway, or 'kill $PID' first."
            exit 1
        fi
    fi
fi

# 3. Create run dir
mkdir -p "$RUN_DIR"
echo "Run dir: $RUN_DIR"

# 4. Env: bump native search cap + cost cap defaults
export WINDY_DAILY_SEARCH_CAP="${WINDY_DAILY_SEARCH_CAP:-500}"
export PYTHONUNBUFFERED=1

# 5. Locate python (prefer repo venv)
PY="$REPO/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    PY="$(command -v python3)"
fi
echo "Python: $PY"

# 6. Launch harness via nohup so it survives shell logout
LOG="$RUN_DIR/harness.log"
PID_FILE="$RUN_DIR/harness.pid"

cd "$REPO"
nohup "$PY" "$REPO/scripts/night_stress/run.py" \
    --run-dir "$RUN_DIR" \
    --sleep "${SLEEP:-60}" \
    --max-prompts "${MAX_PROMPTS:-201}" \
    --max-cost "${MAX_COST:-15.0}" \
    > "$LOG" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"

# 7. Wait briefly + verify it stayed alive
sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: harness died within 3s. Last log lines:"
    tail -20 "$LOG"
    exit 1
fi

echo ""
echo "✅ Stress harness launched."
echo "   pid:        $PID"
echo "   run dir:    $RUN_DIR"
echo "   log:        $LOG"
echo "   findings:   $RUN_DIR/findings.jsonl"
echo "   CURRENT:    $STRESS_DIR/CURRENT_RUN -> $RUN_DIR"
echo ""
echo "To halt the harness: touch $STRESS_DIR/STOP   (graceful)"
echo "To halt repair loop: touch $STRESS_DIR/PAUSE_REPAIR"
echo "To monitor:          tail -f $LOG"
echo "                     tail -f $RUN_DIR/findings.jsonl"
echo "To analyze:          $PY $REPO/scripts/night_stress/analyze.py"
