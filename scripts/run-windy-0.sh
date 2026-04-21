#!/usr/bin/env bash
# Boot Grant's personal Windy 0 dog-food instance on Telegram.
# Expects ZAI_API_KEY + TELEGRAM_BOT_TOKEN already exported (source them
# from kit-army-config/ACCESS_LOCKBOX.md before calling — never commit).
set -euo pipefail

cd "$(dirname "$0")/.."

: "${ZAI_API_KEY:?ZAI_API_KEY not set — see ACCESS_LOCKBOX §2 (Z.AI / ZhipuAI)}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set — see ACCESS_LOCKBOX §5 (Windy 0)}"

# python-dotenv runs with override=False, so anything we export here wins
# over the repo's .env. The shared .env pins DEFAULT_MODEL to a Claude
# model and ships a placeholder ANTHROPIC_API_KEY — neither belongs in
# the Windy 0 instance.
export DEFAULT_MODEL=glm-4.7
export ANTHROPIC_API_KEY=""
# Same class of fix: .env's WINDYFLY_DB_PATH=data/windyfly.db was
# silently overriding windy-0.toml's data/windy-0.db, so /pulse
# reported the wrong DB and the bot wrote into the shared windyfly.db.
export WINDYFLY_DB_PATH=data/windy-0.db

uv sync --extra telegram --quiet
exec uv run python -m windyfly.main --channel telegram --config windy-0.toml
