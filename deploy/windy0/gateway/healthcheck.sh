#!/usr/bin/env bash
# healthcheck.sh wait   — block until /api/health answers (ExecStartPost), max 30 s
# healthcheck.sh check  — one probe; after 2 consecutive failures, restart the unit
set -uo pipefail
URL="http://${GATEWAY_HOST:-127.0.0.1}:${GATEWAY_PORT:-3000}/api/health"
STATE="${XDG_RUNTIME_DIR:-/tmp}/windy-gateway-health.fails"
ok() { curl -fsS --max-time 5 "$URL" 2>/dev/null | grep -q '"status":"ok"'; }
case "${1:-check}" in
  wait)
    for _ in $(seq 1 60); do ok && exit 0; sleep 0.5; done
    echo "gateway did not answer $URL within 30s" >&2; exit 1 ;;
  check)
    if ok; then rm -f "$STATE"; exit 0; fi
    n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE"
    logger -p user.warning -t windy-gateway-health "health probe failed ($n) at $URL"
    if (( n >= 2 )); then
      logger -p user.err -t windy-gateway-health "restarting windy-gateway.service after $n failed probes"
      systemctl --user restart windy-gateway.service; rm -f "$STATE"
    fi
    exit 1 ;;
  *) echo "usage: $0 wait|check" >&2; exit 2 ;;
esac
