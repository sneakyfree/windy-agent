#!/usr/bin/env bash
# Register the bots commands with Telegram via the setMyCommands
# API. After this runs, when a user taps "/" in Telegram they see
# a menu of available commands instead of having to know magic
# words like /reset.
#
# Run once per bot deployment. Idempotent — safe to re-run after
# updating the command list.
#
# Usage:
#   bash scripts/setup-telegram-commands.sh

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    echo "FATAL: TELEGRAM_BOT_TOKEN not set" >&2
    exit 2
fi

# The grandma-discoverable command set. Kept short and friendly:
# every entry is something a non-technical user would benefit from
# tapping. Engineering commands (selftest, doctor, etc.) stay
# typed-only.
#
# Telegram caps descriptions at 256 chars; we keep them under 60
# for clean rendering on phones.
read -r -d "" COMMANDS <<'JSON' || true
[
  {
    "command": "reset",
    "description": "Restart me if I am acting weird (memory stays safe)"
  },
  {
    "command": "health",
    "description": "How am I doing? (organ status + recommendations)"
  },
  {
    "command": "budget",
    "description": "How much have I spent today?"
  },
  {
    "command": "help",
    "description": "Show what I can do"
  },
  {
    "command": "panic",
    "description": "Emergency reset (same as /reset)"
  }
]
JSON

HTTP_CODE=$(curl -sS -o /tmp/setmycommands.out -w "%{http_code}" \
    --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
    -H "Content-Type: application/json" \
    -d "{\"commands\":$COMMANDS}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    echo "FAIL: http=$HTTP_CODE"
    cat /tmp/setmycommands.out
    rm -f /tmp/setmycommands.out
    exit 1
fi

if ! grep -q '"ok":true' /tmp/setmycommands.out; then
    echo "FAIL: Telegram rejected the request"
    cat /tmp/setmycommands.out
    rm -f /tmp/setmycommands.out
    exit 1
fi
rm -f /tmp/setmycommands.out

echo "✅ Telegram command menu registered."
echo
echo "When users tap / in Telegram, they will see:"
echo "  /reset    Restart me if I am acting weird (memory stays safe)"
echo "  /health   How am I doing? (organ status + recommendations)"
echo "  /budget   How much have I spent today?"
echo "  /help     Show what I can do"
echo "  /panic    Emergency reset (same as /reset)"
echo
echo "Telegram caches this menu — may take ~30 seconds to appear in"
echo "the chat UI. Restart the Telegram app if it does not refresh."
