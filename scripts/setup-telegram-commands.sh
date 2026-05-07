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
# CATEGORIZATION (PR #139, 2026-05-07):
# Telegram's setMyCommands API gives us a flat list — no native
# category grouping. So we encode categories as the leading emoji
# of each description. Grandma's eye scans down the left column,
# sees clusters of identical emojis, and her gaze lands on the
# right group fast even when squinting.
#
# Order of categories (top to bottom, urgency-weighted):
#   🆘 Rescue        — bot broken / panic
#   💬 Conversation  — flow control
#   💰 Money         — spend control
#   🧠 Memory        — what I know
#   🎭 Personality   — how I behave
#   ℹ  Status        — am I OK?
#   🤖 Model         — which brain
#   🪪 Identity      — who I am
#   👵 Demo          — tour mode
#
# Telegram caps descriptions at 256 chars; we keep them under 60
# for clean rendering on phones.
#
# Voice messages are handled by the channel adapter automatically;
# no command needed. Install voice support with:
#   pip install windyfly[voice]
# Then restart the bot. Without it, voice notes get a polite "voice
# isn't installed" reply rather than the silent drop pre-PR #129.

read -r -d "" COMMANDS <<'JSON' || true
[
  {"command": "reset",     "description": "🆘 Restart me if I'm stuck or acting weird"},
  {"command": "resurrect", "description": "🆘 Save me — switch to a free local model"},
  {"command": "normal",    "description": "🆘 Back to my usual brain after /resurrect"},
  {"command": "auto-resurrect", "description": "🆘 Toggle auto-switch to free model on rate limit"},
  {"command": "help",      "description": "🆘 Show what I can do for you"},
  {"command": "health",    "description": "🆘 How am I doing right now?"},

  {"command": "new",       "description": "💬 Start a fresh conversation (memory stays)"},
  {"command": "history",   "description": "💬 Show the last 10 messages"},
  {"command": "summarize", "description": "💬 Summarize this conversation"},
  {"command": "undo",      "description": "💬 Undo the last exchange"},
  {"command": "retry",     "description": "💬 Regenerate the last reply"},
  {"command": "continue",  "description": "💬 Continue if a reply got cut off"},

  {"command": "spend",     "description": "💰 Today's spending by provider"},
  {"command": "pause",     "description": "💰 Stop me from spending money"},
  {"command": "resume",    "description": "💰 Wake me up after a pause"},
  {"command": "yolo",      "description": "💰 Let me cook hard (24h, no auto-pause)"},
  {"command": "yolo24",    "description": "💰 YOLO mode for 24 hours"},
  {"command": "yolo48",    "description": "💰 YOLO mode for 48 hours"},

  {"command": "facts",     "description": "🧠 What I remember about you"},
  {"command": "memory",    "description": "🧠 Memory tools (stats and search)"},
  {"command": "intents",   "description": "🧠 Your active goals and intents"},

  {"command": "mood",      "description": "🎭 What mood I'm picking up from you"},
  {"command": "soul",      "description": "🎭 Show my personality"},
  {"command": "preset",    "description": "🎭 Switch personality preset"},
  {"command": "sliders",   "description": "🎭 Show all personality sliders"},

  {"command": "status",    "description": "ℹ️ Quick status summary"},
  {"command": "version",   "description": "ℹ️ Git SHA, branch, uptime — am I latest?"},
  {"command": "uptime",    "description": "ℹ️ How long I've been running"},
  {"command": "ping",      "description": "ℹ️ Am I responsive?"},
  {"command": "pulse",     "description": "ℹ️ Live runtime diagnostics"},

  {"command": "fast",      "description": "🤖 Switch to my fastest / cheapest model"},
  {"command": "model",     "description": "🤖 Show or switch my LLM"},
  {"command": "tokens",    "description": "🤖 Token usage this session"},

  {"command": "whoami",    "description": "🪪 My identity (passport, role)"},

  {"command": "guest",     "description": "👵 Switch into grandma-mode for a demo"}
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
python3 -c "
import json
cmds = json.loads('''$COMMANDS''')
for c in cmds:
    print(f\"  /{c['command']:8}  {c['description']}\")
"
echo
echo "Telegram caches this menu — may take ~30 seconds to appear in"
echo "the chat UI. Restart the Telegram app if it does not refresh."
