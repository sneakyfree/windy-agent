#!/usr/bin/env bash
# ============================================================================
# scripts/smoke-test.sh — Wave 9 launch-prep smoke test
# ============================================================================
#
# Exercises the full-path happy flow against a running agent + gateway:
#
#   1. `windy test` — the self-test command (maps to cli_selftest.run_self_test).
#      We'd call `windy selftest --full` here; that form is aspirational until
#      the selftest command grows a --full flag. For now the existing self-test
#      is the closest equivalent.
#   2. GET  /api/health                  — gateway liveness
#   3. POST /hatch/remote                — retired in 0.7.5 (ADR-059): must
#                                          answer 410 {"error":"hatch_moved"}.
#                                          Agents hatch only in the Windy
#                                          hatch ceremony (`windy go`).
#
# Usage:
#
#   bash scripts/smoke-test.sh                   # defaults to localhost
#   bash scripts/smoke-test.sh https://my-vps    # run against a remote VPS
#
# Environment overrides (handy for CI):
#
#   GATEWAY_URL                    override base URL
#
# Exit codes:
#   0 — all checks passed
#   1 — self-test failed
#   2 — gateway health check failed
#   3 — /hatch/remote did not answer 410 hatch_moved
# ============================================================================

set -u -o pipefail

# ── Config ──────────────────────────────────────────────────────────────────

GATEWAY_URL="${GATEWAY_URL:-${1:-http://localhost:3000}}"
GATEWAY_URL="${GATEWAY_URL%/}"  # strip trailing slash

# ── Helpers ─────────────────────────────────────────────────────────────────

c_red()   { printf '\033[31m%s\033[0m' "$*"; }
c_green() { printf '\033[32m%s\033[0m' "$*"; }
c_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
c_dim()   { printf '\033[2m%s\033[0m' "$*"; }

step()    { printf '\n%s %s\n' "$(c_yellow '▸')" "$*"; }
pass()    { printf '  %s %s\n'   "$(c_green '✓')" "$*"; }
fail()    { printf '  %s %s\n'   "$(c_red   '✗')" "$*"; }
note()    { printf '  %s %s\n'   "$(c_dim   '·')" "$*"; }

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "missing dependency: $1"
    exit 1
  fi
}

require curl
require awk
require grep

# ── Step 1: self-test ───────────────────────────────────────────────────────

step "windy test (agent self-test)"

# cli_selftest doesn't ship a --full flag today. We try it optimistically —
# if argparse rejects, we fall back to the plain form.
if command -v windy >/dev/null 2>&1; then
  if windy test --full >/tmp/smoke-selftest.log 2>&1 || windy test >/tmp/smoke-selftest.log 2>&1; then
    pass "self-test passed"
    note "log: /tmp/smoke-selftest.log"
  else
    fail "self-test failed — see /tmp/smoke-selftest.log"
    exit 1
  fi
else
  note "skipping — 'windy' CLI not on PATH (run inside the agent's venv)"
fi

# ── Step 2: gateway health ──────────────────────────────────────────────────

step "gateway health @ ${GATEWAY_URL}"

http_code="$(curl -s -o /tmp/smoke-health.json -w '%{http_code}' \
    --max-time 10 "${GATEWAY_URL}/api/health" || true)"

if [ "${http_code}" = "200" ] && grep -q '"status":"ok"' /tmp/smoke-health.json; then
  pass "gateway healthy (HTTP ${http_code})"
else
  fail "gateway health failed (HTTP ${http_code:-no-response})"
  [ -s /tmp/smoke-health.json ] && note "$(cat /tmp/smoke-health.json)"
  exit 2
fi

# ── Step 3: the retired remote hatch answers 410 ────────────────────────────

step "POST /hatch/remote → 410 hatch_moved"

http_code="$(curl -s -o /tmp/smoke-hatch.json -w '%{http_code}' \
    --max-time 10 -X POST -H "Content-Type: application/json" -d '{}' \
    "${GATEWAY_URL}/hatch/remote" || true)"

if [ "${http_code}" = "410" ] && grep -q '"error":"hatch_moved"' /tmp/smoke-hatch.json; then
  pass "remote hatch retired (HTTP 410 hatch_moved)"
else
  fail "expected HTTP 410 hatch_moved, got HTTP ${http_code:-no-response}"
  [ -s /tmp/smoke-hatch.json ] && note "$(cat /tmp/smoke-hatch.json)"
  exit 3
fi

# ── Done ────────────────────────────────────────────────────────────────────

printf '\n%s %s\n\n' "$(c_green '✓')" "$(c_green 'smoke test passed')"
exit 0
