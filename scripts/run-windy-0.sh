#!/usr/bin/env bash
# Boot Grant's personal Windy 0 dog-food instance on Telegram.
# Expects ANTHROPIC_API_KEY + TELEGRAM_BOT_TOKEN already exported (the
# launchd plist sources ~/.windy/windy-0.env before exec'ing this).
set -euo pipefail

cd "$(dirname "$0")/.."

: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY not set — populate ~/.windy/windy-0.env}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set — see ACCESS_LOCKBOX §5 (Windy 0)}"

# python-dotenv runs with override=False, so anything we export here wins
# over the repo's shared .env. Pin the model so the .env's default
# can't quietly downgrade us.
export DEFAULT_MODEL=claude-sonnet-4-6
# Same class of fix: .env's WINDYFLY_DB_PATH=data/windyfly.db was
# silently overriding windy-0.toml's data/windy-0.db, so /pulse
# reported the wrong DB and the bot wrote into the shared windyfly.db.
export WINDYFLY_DB_PATH=data/windy-0.db

uv sync --extra telegram --quiet
exec uv run python -m windyfly.main --channel telegram --config windy-0.toml
