#!/usr/bin/env bash
# Pre-show consolidated checklist. Runs every verification a tour
# operator wants before walking on stage, in one shot, with a clean
# green/red summary. Takes ~2 minutes (mostly the v12 dry-run).
#
# Designed to be run 30 minutes before any show. Output is grouped
# so you can scan it at a glance.
#
# Usage:
#   bash scripts/windy-tour-checklist.sh
#
# Exits 0 if everything is green. Exits 1 if anything blocks the
# demo. Read the per-section detail to see what.

set -uo pipefail

PASS_GLYPH="✅"
FAIL_GLYPH="❌"
WARN_GLYPH="⚠️ "

OVERALL_OK=1
ISSUES=()

_DEFAULT_AGENT_DIR="/home/grantwhitmer/Desktop/Grant"\'"s Folder/windy-agent"
AGENT_DIR="${WINDY_AGENT_DIR:-$_DEFAULT_AGENT_DIR}"

print_header() {
    echo
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Windy Fly — Pre-Show Tour Checklist"
    echo "  $(date '+%a %b %d %H:%M:%S %Z')"
    echo "═══════════════════════════════════════════════════════════════"
}

check() {
    local name="$1"; local cmd="$2"; local expect="${3:-}"
    local out
    out="$(bash -c "$cmd" 2>&1)"
    local rc=$?
    if [[ $rc -ne 0 ]] && [[ -z "$expect" ]]; then
        echo "  $FAIL_GLYPH $name"
        echo "       $out" | head -3
        ISSUES+=("$name failed")
        OVERALL_OK=0
        return
    fi
    if [[ -n "$expect" ]] && [[ "$out" != *"$expect"* ]]; then
        echo "  $FAIL_GLYPH $name"
        echo "       expected: $expect"
        echo "       got:      $out" | head -2
        ISSUES+=("$name: expected $expect")
        OVERALL_OK=0
        return
    fi
    echo "  $PASS_GLYPH $name"
}

print_header

# ── Section 1: Process-level health ───────────────────────────────
echo
echo "── Section 1: Bot process & supervision ──"
check "windy-0.service active" \
      "systemctl --user is-active windy-0.service" \
      "active"
check "Restart=always armed" \
      "systemctl --user show windy-0.service -p Restart --value" \
      "always"
check "Watchdog active (10 min)" \
      "systemctl --user show windy-0.service -p WatchdogUSec --value" \
      "10min"

# ── Section 2: Telegram connectivity ──────────────────────────────
echo
echo "── Section 2: Telegram connectivity ──"
check "Liveness probe running" \
      "systemctl --user is-active windy-liveness-probe.timer" \
      "active"
check "Liveness probe last result OK" \
      "awk -F= '/^result=/{print \$2}' ~/.windy/liveness.status" \
      "OK"
HEARTBEAT_LINE=$(grep "polling=alive" ~/.windy/windy-0.log 2>/dev/null | tail -1 || echo "")
if [[ -n "$HEARTBEAT_LINE" ]]; then
    HEARTBEAT_TS=$(echo "$HEARTBEAT_LINE" | awk '{print $1}')
    NOW_S=$(date +%s)
    HB_S=$(date -d "$(date +%Y-%m-%d) $HEARTBEAT_TS" +%s 2>/dev/null || echo 0)
    AGE=$(( (NOW_S - HB_S + 86400) % 86400 ))
    if (( AGE < 600 )); then
        echo "  $PASS_GLYPH Heartbeat fresh (${AGE}s ago)"
    else
        echo "  $WARN_GLYPH Heartbeat ${AGE}s old (last check 10+ min ago)"
        ISSUES+=("heartbeat stale")
    fi
else
    echo "  $FAIL_GLYPH No heartbeat line in log"
    ISSUES+=("no heartbeat")
    OVERALL_OK=0
fi

# ── Section 3: All 4 weekly timers active ─────────────────────────
echo
echo "── Section 3: Self-improvement timers ──"
for t in windy-weekly-brief windy-redalarm windy-evening-recap windy-health-purge; do
    check "$t.timer active" \
          "systemctl --user is-active $t.timer" \
          "active"
done

# ── Section 4: Health snapshot freshness ──────────────────────────
echo
echo "── Section 4: Latest organ scorecard ──"
LATEST_SCORECARD=$(ls -t ~/.windy-stress/health/*.json 2>/dev/null | head -1)
if [[ -n "$LATEST_SCORECARD" ]]; then
    AGE_DAYS=$(( ($(date +%s) - $(stat -c %Y "$LATEST_SCORECARD")) / 86400 ))
    if (( AGE_DAYS < 8 )); then
        echo "  $PASS_GLYPH Scorecard recent (${AGE_DAYS}d old)"
        # Quick read of organ counts
        python3 -c "
import json, sys
d = json.load(open('$LATEST_SCORECARD'))
c = d.get('verdict_counts', {})
print(f\"       🟢 {c.get('green',0)} green · 🟡 {c.get('yellow',0)} yellow · 🔴 {c.get('red',0)} red\")
" 2>/dev/null || true
    else
        echo "  $WARN_GLYPH Latest scorecard is ${AGE_DAYS}d old"
        ISSUES+=("scorecard stale")
    fi
else
    echo "  $WARN_GLYPH No scorecards yet (will accumulate post-tour)"
fi

# ── Section 5: v12 demo dry-run (the big one) ─────────────────────
echo
echo "── Section 5: v12 demo dry-run (real Haiku, ~\$0.10) ──"
DRYRUN_LOG=$(mktemp)
(
    set -a
    # shellcheck disable=SC1091
    source "${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}" 2>/dev/null
    set +a
    export WINDYFLY_CONFIG="${WINDYFLY_CONFIG:-/home/grantwhitmer/.windy-stress/config.toml}"
    export DEFAULT_MODEL="${DEFAULT_MODEL:-claude-haiku-4-5-20251001}"
    "$AGENT_DIR/.venv/bin/python" \
        /home/grantwhitmer/.windy-stress/stress_v12_demo_dryrun.py
) > "$DRYRUN_LOG" 2>&1

if grep -q "DEMO READY" "$DRYRUN_LOG"; then
    echo "  $PASS_GLYPH 5/5 demo beats green"
    grep -E "✓|✗" "$DRYRUN_LOG" | sed 's/^/       /'
elif grep -q "DEMO HAS ISSUES" "$DRYRUN_LOG"; then
    echo "  $FAIL_GLYPH dry-run reports issues"
    grep -E "✓|✗" "$DRYRUN_LOG" | sed 's/^/       /'
    ISSUES+=("v12 dry-run failures")
    OVERALL_OK=0
else
    echo "  $FAIL_GLYPH dry-run did not complete"
    tail -8 "$DRYRUN_LOG" | sed 's/^/       /'
    ISSUES+=("v12 dry-run did not complete")
    OVERALL_OK=0
fi
rm -f "$DRYRUN_LOG"

# ── Final verdict ─────────────────────────────────────────────────
echo
echo "═══════════════════════════════════════════════════════════════"
if (( OVERALL_OK == 1 )); then
    echo "  $PASS_GLYPH READY FOR STAGE — every section green."
    echo
    echo "  Open Telegram, walk on, follow docs/TOUR_DEMO_SCRIPT.md."
    echo "═══════════════════════════════════════════════════════════════"
    exit 0
else
    echo "  $FAIL_GLYPH NOT READY — issues need attention before stage:"
    for issue in "${ISSUES[@]}"; do
        echo "       • $issue"
    done
    echo
    echo "  Quick recovery:"
    echo "    1. Hit /reset on the bot from your phone."
    echo "    2. Wait 30 seconds for it to come back."
    echo "    3. Re-run: bash scripts/windy-tour-checklist.sh"
    echo
    echo "  If still failing, see docs/TOUR_DEMO_SCRIPT.md → "
    echo "  'If something goes wrong on stage' for in-character recovery."
    echo "═══════════════════════════════════════════════════════════════"
    exit 1
fi
