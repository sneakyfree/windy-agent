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
#   3. POST /hatch/remote                — drives the SSE ceremony with a
#                                          broker_token and captures the event
#                                          stream
#   4. Parse the SSE stream and verify every one of the 13 canonical events
#      fires in the contract order (src/windyfly/hatch_remote.EVENT_ORDER).
#
# Usage:
#
#   bash scripts/smoke-test.sh                   # defaults to localhost
#   bash scripts/smoke-test.sh https://my-vps    # run against a remote VPS
#
# Environment overrides (handy for CI):
#
#   GATEWAY_URL                    override base URL
#   SMOKE_BROKER_TOKEN             pre-minted broker token (skips live mint)
#   SMOKE_WINDY_IDENTITY_ID        identity to pass to /hatch/remote
#   SMOKE_PASSPORT_NUMBER          passport to claim (test-mode ok)
#   SMOKE_OWNER_EMAIL/PHONE/NAME   owner contact fields for the hatch payload
#   SMOKE_TIMEOUT_SECONDS          how long to wait for hatch.complete (default 120)
#
# Exit codes:
#   0 — every canonical event fired in order; hatch.complete observed
#   1 — self-test failed
#   2 — gateway health check failed
#   3 — /hatch/remote HTTP failure
#   4 — SSE event ordering violated
#   5 — hatch.complete not seen within timeout
# ============================================================================

set -u -o pipefail

# ── Config ──────────────────────────────────────────────────────────────────

GATEWAY_URL="${GATEWAY_URL:-${1:-http://localhost:3000}}"
GATEWAY_URL="${GATEWAY_URL%/}"  # strip trailing slash

TIMEOUT="${SMOKE_TIMEOUT_SECONDS:-120}"

# Contract-pinned event order. MUST match src/windyfly/hatch_remote.EVENT_ORDER.
CANONICAL_EVENTS=(
  "eternitas.registering"
  "eternitas.registered"
  "mail.provisioning"
  "mail.provisioned"
  "chat.provisioning"
  "chat.provisioned"
  "cloud.provisioning"
  "cloud.provisioned"
  "phone.assigning"
  "phone.assigned"
  "birth_certificate.generating"
  "birth_certificate.ready"
  "hatch.complete"
)

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

# ── Step 3: POST /hatch/remote and capture SSE ──────────────────────────────

step "POST /hatch/remote → SSE stream"

BROKER_TOKEN="${SMOKE_BROKER_TOKEN:-wk_broker_smoketest_$(date +%s)}"
WINDY_IDENTITY_ID="${SMOKE_WINDY_IDENTITY_ID:-wi_smoke_$(date +%s)}"
PASSPORT_NUMBER="${SMOKE_PASSPORT_NUMBER:-ET26-SMOKE-TEST}"
OWNER_EMAIL="${SMOKE_OWNER_EMAIL:-smoke@example.com}"
OWNER_PHONE="${SMOKE_OWNER_PHONE:-+15555555555}"
OWNER_NAME="${SMOKE_OWNER_NAME:-Smoke Tester}"

REQUEST_BODY=$(cat <<JSON
{
  "windy_identity_id": "${WINDY_IDENTITY_ID}",
  "passport_number":   "${PASSPORT_NUMBER}",
  "broker_token":      "${BROKER_TOKEN}",
  "owner_email":       "${OWNER_EMAIL}",
  "owner_phone":       "${OWNER_PHONE}",
  "owner_name":        "${OWNER_NAME}",
  "agent_name":        "Smoke Agent"
}
JSON
)

SSE_LOG=/tmp/smoke-sse.log
: >"${SSE_LOG}"

# curl --no-buffer keeps SSE frames flowing; --max-time caps the whole run.
# We background it, tail the log for hatch.complete, and kill when seen.
curl -s --no-buffer \
    --max-time "${TIMEOUT}" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -d "${REQUEST_BODY}" \
    "${GATEWAY_URL}/hatch/remote" >"${SSE_LOG}" 2>/tmp/smoke-sse.err &
CURL_PID=$!

waited=0
while [ $waited -lt "${TIMEOUT}" ]; do
  if grep -q "^event: hatch.complete$" "${SSE_LOG}" 2>/dev/null; then
    break
  fi
  if ! kill -0 "${CURL_PID}" 2>/dev/null; then
    break  # curl exited on its own
  fi
  sleep 1
  waited=$((waited + 1))
done

# Nudge curl if it's still hanging.
kill "${CURL_PID}" 2>/dev/null || true
wait "${CURL_PID}" 2>/dev/null || true

if [ ! -s "${SSE_LOG}" ]; then
  fail "no SSE output captured"
  [ -s /tmp/smoke-sse.err ] && note "curl stderr: $(cat /tmp/smoke-sse.err)"
  exit 3
fi

pass "SSE stream captured ($(wc -l <"${SSE_LOG}") lines)"

# ── Step 4: verify canonical event ordering ────────────────────────────────

step "verifying 13 canonical events in order"

# Extract the `event:` field from each SSE frame, in arrival order.
OBSERVED=$(awk '/^event: / { print $2 }' "${SSE_LOG}")
OBSERVED_COUNT=$(printf '%s\n' "${OBSERVED}" | grep -c '.' || true)
note "observed ${OBSERVED_COUNT} event frame(s)"

missing=()
last_index=-1
order_violation=""

for canonical in "${CANONICAL_EVENTS[@]}"; do
  # find the first line number (1-based in OBSERVED) matching this canonical name
  # — awk's exit-on-match keeps it cheap even with a long event log.
  idx=$(printf '%s\n' "${OBSERVED}" | awk -v target="${canonical}" '
    $0 == target { print NR; exit }
  ')

  if [ -z "${idx}" ]; then
    missing+=("${canonical}")
    continue
  fi

  if [ "${idx}" -le "${last_index}" ]; then
    order_violation="${canonical} appeared at position ${idx} — expected after position ${last_index}"
    break
  fi
  pass "${canonical} @ frame ${idx}"
  last_index="${idx}"
done

if [ "${#missing[@]}" -gt 0 ]; then
  fail "missing events: ${missing[*]}"
  printf '  last 20 observed frames:\n'
  printf '%s\n' "${OBSERVED}" | tail -20 | sed 's/^/    /'
  exit 5
fi

if [ -n "${order_violation}" ]; then
  fail "event ordering violated — ${order_violation}"
  exit 4
fi

# ── Done ────────────────────────────────────────────────────────────────────

printf '\n%s %s\n\n' "$(c_green '✓')" "$(c_green 'smoke test passed — all 13 canonical events fired in order')"
exit 0
