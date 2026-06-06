"""Windy Fly configuration loader.

Loads windyfly.toml and merges with environment variables for secrets.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def load_config(path: str = "windyfly.toml") -> dict[str, Any]:
    """Load TOML config file and merge with environment variable overrides.

    Args:
        path: Path to the TOML config file. Defaults to 'windyfly.toml'.

    Returns:
        A dict containing all config values, merged with env overrides.
    """
    load_dotenv()

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

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
