#!/usr/bin/env bash
# Boot Grant's personal Windy 0 dog-food instance on Telegram.
# Expects ZAI_API_KEY + TELEGRAM_BOT_TOKEN already exported (source them
# from kit-army-config/ACCESS_LOCKBOX.md before calling — never commit).
set -euo pipefail

cd "$(dirname "$0")/.."

: "${ZAI_API_KEY:?ZAI_API_KEY not set — see ACCESS_LOCKBOX §2 (Z.AI / ZhipuAI)}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set — see ACCESS_LOCKBOX §5 (Windy 0)}"

uv sync --extra telegram --quiet
exec uv run python -m windyfly.main --channel telegram --config windy-0.toml
