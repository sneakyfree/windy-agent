"""Windy Fly configuration loader.

Loads windyfly.toml and merges with environment variables for secrets.

Resilience contract (2026-07-04 audit): a corrupt config file must never
crash-loop the agent. If the TOML cannot be parsed, the loader logs the
problem loudly, falls back to ``DEFAULT_CONFIG``, applies env overrides
as usual, and records the parse error under ``_config_error`` so boot
logging and ``/status`` can tell the user what happened. A *missing*
file still raises ``FileNotFoundError`` — booting a mystery agent from
the wrong directory on silent defaults would hide real setup mistakes,
and main.py already turns that into a clean, actionable exit.
"""

from __future__ import annotations

import copy
import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Safe boot skeleton used when windyfly.toml is unreadable. Mirrors the
# sections of the reference windyfly.toml; values here only need to be
# good enough to boot, reach the channels (tokens come from env/.env),
# and let the agent tell its user the config file needs attention.
DEFAULT_CONFIG: dict[str, Any] = {
    "agent": {
        "name": "Windy Fly",
        "default_model": "claude-sonnet-5",
        "max_context_tokens": 8000,
        "max_response_tokens": 2000,
    },
    "memory": {
        "db_path": "data/windyfly.db",
        "max_episodes_per_context": 20,
        "max_nodes_per_context": 10,
    },
    "personality": {
        "soul_path": "SOUL.md",
        "preset": "buddy",
    },
    "costs": {
        "daily_budget_usd": 5.0,
        "monthly_budget_usd": 50.0,
        "warn_at_percent": 80,
    },
    "updates": {
        "auto_check": True,
        "auto_install": False,
        "check_interval": 86400,
    },
    "channels": {},
    "ecosystem": {},
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load TOML config file and merge with environment variable overrides.

    Args:
        path: Path to the TOML config file. When omitted, honors the
            ``WINDYFLY_CONFIG`` env var and falls back to 'windyfly.toml'.

    Returns:
        A dict containing all config values, merged with env overrides.
        On a corrupt (unparseable) file, returns ``DEFAULT_CONFIG`` with
        env overrides applied and ``_config_error`` set to the parse
        error text.

    Raises:
        FileNotFoundError: when the config file does not exist.
    """
    load_dotenv()

    if path is None:
        path = os.environ.get("WINDYFLY_CONFIG", "windyfly.toml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as e:
        logger.error(
            "Config file %s is unreadable (%s) — booting on safe defaults. "
            "Fix or regenerate it (e.g. 'windy go') to restore your settings.",
            config_path,
            e,
        )
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["_config_error"] = f"{type(e).__name__}: {e}"

    # Merge environment variable overrides for secrets and runtime config
    env_overrides: dict[str, tuple[list[str], type]] = {
        "OPENAI_API_KEY": (["openai_api_key"], str),
        "ANTHROPIC_API_KEY": (["anthropic_api_key"], str),
        "DEFAULT_MODEL": (["agent", "default_model"], str),
        "WINDYFLY_DB_PATH": (["memory", "db_path"], str),
        "LOG_LEVEL": (["log_level"], str),
        "MATRIX_HOMESERVER": (["matrix", "homeserver"], str),
        "MATRIX_BOT_USER": (["matrix", "bot_user"], str),
        "MATRIX_BOT_TOKEN": (["matrix", "bot_token"], str),
        "MATRIX_BOT_PASSWORD": (["matrix", "bot_password"], str),
        "WINDY_API_URL": (["windy_api", "base_url"], str),
        "WINDY_JWT": (["windy_api", "jwt"], str),
        # ETERNITAS_API_URL is the legacy name (back-compat); ETERNITAS_URL
        # is canonical and, being listed later, wins when both are set.
        "ETERNITAS_API_URL": (["ecosystem", "eternitas_url"], str),
        "ETERNITAS_URL": (["ecosystem", "eternitas_url"], str),
        "WINDYMAIL_API_URL": (["ecosystem", "windy_mail_url"], str),
        "WINDY_CLOUD_URL": (["ecosystem", "windy_cloud_url"], str),
        "WINDY_PRO_URL": (["ecosystem", "windy_pro_url"], str),
        "ANTHROPIC_OAUTH_ACCESS_TOKEN": (["anthropic_oauth_access_token"], str),
        "ANTHROPIC_OAUTH_REFRESH_TOKEN": (["anthropic_oauth_refresh_token"], str),
        "ANTHROPIC_OAUTH_EXPIRES_AT": (["anthropic_oauth_expires_at"], str),
    }

    for env_key, (key_path, value_type) in env_overrides.items():
        env_value = os.environ.get(env_key)
        if env_value:
            _set_nested(config, key_path, value_type(env_value))

    return config


def _set_nested(d: dict, keys: list[str], value: Any) -> None:
    """Set a value in a nested dict using a list of keys."""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value
