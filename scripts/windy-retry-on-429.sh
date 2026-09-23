#!/usr/bin/env bash
# windy-retry-on-429.sh — make a weekly health job rate-limit-aware.
#
#   windy-retry-on-429.sh <job-name> [--status-file <path>] -- <command…>
#
# Why: on 2026-09-20 windy-fire-drill (engine_swap FAIL) and
# windy-continuity-battery (6%) both "failed" because Anthropic returned
# 429 and the agent fell back to the local lifeboat model. That's a rate
# limit, not a regression, but under windy-job (ok = exit 0) it would page.
#
# Rule:
#   * command exits 0                          → exit 0 (no retry)
#   * command fails AND its output shows an
#     Anthropic 429 ("Error code: 429" /
#     "rate_limit_error")                      → wait, re-run (up to 2 retries)
#   * command fails WITHOUT any 429 marker     → exit with its code at once
#     (a real failure pages, never retried or masked)
#   * still 429-failing after the last retry   → print
#       RATE-LIMITED: skipped (Anthropic 429 x<n>), not a regression
#     mark the status file, exit 0 (the heartbeat shows ok:true + that detail)
#
# A deliberate 401 (the fire drill's fake-dead-key lifeboat step) is NOT a
# 429 marker, so it never triggers a retry.
#
# Env: WINDY_429_BACKOFFS="300 900" (seconds before retry 1, 2)
set -uo pipefail

name="${1:?usage: windy-retry-on-429.sh <job-name> [--status-file F] -- cmd…}"; shift
status_file=""
while [[ $# -gt 0 && "$1" != "--" ]]; do
    case "$1" in
        --status-file) status_file="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ "${1:-}" == "--" ]] && shift
[[ $# -gt 0 ]] || { echo "no command given" >&2; exit 2; }

read -r -a backoffs <<< "${WINDY_429_BACKOFFS:-300 900}"
MARKER='Error code: 429|rate_limit_error'
attempts=$(( ${#backoffs[@]} + 1 ))
log="$(mktemp)"; trap 'rm -f "$log"' EXIT

for (( i = 1; i <= attempts; i++ )); do
    : > "$log"
    "$@" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    if [[ $rc -eq 0 ]]; then
        [[ $i -gt 1 ]] && echo "[$name] passed on attempt $i after Anthropic 429s"
        exit 0
    fi
    if ! grep -qE "$MARKER" "$log"; then
        echo "[$name] failed (exit $rc) with no Anthropic 429 in its output: a real failure, not retrying"
        exit "$rc"
    fi
    if [[ $i -lt attempts ]]; then
        wait_s=${backoffs[$((i - 1))]}
        echo "[$name] attempt $i failed with Anthropic 429 (exit $rc); retrying in ${wait_s}s"
        logger -t "$name" "attempt $i hit Anthropic 429; retrying in ${wait_s}s" 2>/dev/null || true
        sleep "$wait_s"
    fi
done

msg="RATE-LIMITED: skipped (Anthropic 429 x${attempts}), not a regression"
echo "$msg"
logger -t "$name" "$msg" 2>/dev/null || true
if [[ -n "$status_file" ]]; then
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    mkdir -p "$(dirname "$status_file")"
    tmp="$(mktemp "${status_file}.XXXX")"
    {
        echo "ts=$now"
        echo "result=RATE_LIMITED"
        echo "detail=$msg"
        # keep the last run's per-step lines (the fire drill writes step_*=…)
        [[ -f "$status_file" ]] && grep -E '^step_' "$status_file" || true
    } > "$tmp"
    mv "$tmp" "$status_file"
fi
exit 0
