#!/usr/bin/env bash
# Install the Windy 0 gateway units. By default this only COPIES files:
# nothing is enabled, started or routed. Pass --enable at go-live.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$HOME/.config/windy-gateway.env"
mkdir -p "$UNIT_DIR"
install -m 644 "$HERE"/windy-gateway.service "$HERE"/windy-gateway-health.service \
               "$HERE"/windy-gateway-health.timer "$UNIT_DIR"/
if [[ ! -f "$ENV_FILE" ]]; then
  install -m 600 "$HERE/windy-gateway.env.example" "$ENV_FILE"
  echo "created $ENV_FILE from the example — fill in WINDY_IDENTITY_ID"
fi
systemctl --user daemon-reload
if [[ "${1:-}" == "--enable" ]]; then
  grep -qE '^WINDY_IDENTITY_ID=.+' "$ENV_FILE" || { echo "WINDY_IDENTITY_ID is empty in $ENV_FILE — refusing to enable" >&2; exit 1; }
  command -v ~/.bun/bin/bun >/dev/null || { echo "bun not found at ~/.bun/bin/bun" >&2; exit 1; }
  (cd "$HOME/.local/share/windyfly/agent/gateway" && ~/.bun/bin/bun install --frozen-lockfile)
  systemctl --user enable --now windy-gateway.service windy-gateway-health.timer
  systemctl --user --no-pager status windy-gateway.service | head -5
else
  echo "installed (not enabled). Go-live: see deploy/windy0/gateway/README.md"
fi
