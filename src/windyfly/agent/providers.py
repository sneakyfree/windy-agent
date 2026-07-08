"""LLM Provider Registry.

Config-driven provider management. Add any OpenAI-compatible lab with just
a base_url and api_key. Anthropic gets its own path (different API format).

Providers are defined in windyfly.toml under [providers.*] and can be
managed at runtime via the Trust Dashboard or REST API.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Built-in provider defaults — zero config needed for these.
# Users only need to set the API key env var.
BUILTIN_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI",
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-5.4", "gpt-5.4-pro", "codex", "o3", "o3-mini", "gpt-4o", "gpt-4o-mini"],
    },
    "anthropic": {
        "name": "Anthropic",
        "type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    },
    "grok": {
        "name": "xAI Grok",
        "type": "openai",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "GROK_API_KEY",
        "models": ["grok-3", "grok-3-mini", "grok-2"],
    },
    "gemini": {
        "name": "Google Gemini",
        "type": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GEMINI_API_KEY",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
    },
    "deepseek": {
        "name": "DeepSeek",
        "type": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-r1"],
    },
    "mistral": {
        "name": "Mistral",
        "type": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": ["openrouter/auto", "google/gemini-2.5-pro", "anthropic/claude-sonnet-4"],
    },
    "together": {
        "name": "Together AI",
        "type": "openai",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "models": ["meta-llama/Llama-3-70b-chat-hf", "mistralai/Mixtral-8x7B-Instruct-v0.1"],
    },
    "groq": {
        "name": "Groq",
        "type": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
    },
    "perplexity": {
        "name": "Perplexity",
        "type": "openai",
        "base_url": "https://api.perplexity.ai",
        "api_key_env": "PERPLEXITY_API_KEY",
        "models": ["sonar-pro", "sonar"],
    },
    "fireworks": {
        "name": "Fireworks AI",
        "type": "openai",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "models": ["accounts/fireworks/models/llama-v3p1-70b-instruct"],
    },
    "kimi": {
        "name": "Moonshot Kimi",
        "type": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": ["kimi-k2.5", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
    },
    "zai": {
        "name": "Z.AI (Zhipu GLM)",
        "type": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZAI_API_KEY",
        "models": ["glm-4.7", "glm-4", "glm-4-flash"],
    },
    "ollama": {
        "name": "Ollama (Local)",
        "type": "openai",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "models": ["llama3", "mistral", "codellama"],
    },
}

# Runtime overrides file — written by the dashboard / API
_OVERRIDES_PATH = Path(os.environ.get(
    "WINDYFLY_PROVIDERS_PATH",
    "data/providers.json",
))


def _load_overrides() -> dict[str, dict[str, Any]]:
    """Load provider overrides from disk (set via dashboard)."""
    if _OVERRIDES_PATH.exists():
        try:
            return json.loads(_OVERRIDES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_overrides(overrides: dict[str, dict[str, Any]]) -> None:
    """Persist provider overrides to disk."""
    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2))


def get_all_providers(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return merged provider list: builtins + toml config + runtime overrides.

    Priority: runtime overrides > toml config > builtins.
    """
    # Copy each provider dict — a plain dict(BUILTIN_PROVIDERS) shares the
    # nested per-provider dicts, so the env-resolution below would write
    # api_key into the module-level builtins and freeze the first-seen
    # token for the process lifetime. With rotating credentials (Claude
    # Code's OAuth token refresh) that meant every call after a rotation
    # 401'd with the fossilized key (windy-0 went dark, 2026-07-08).
    providers = {key: dict(val) for key, val in BUILTIN_PROVIDERS.items()}

    # Merge from windyfly.toml [providers.*]
    if config:
        toml_providers = config.get("providers", {})
        for key, val in toml_providers.items():
            if key in providers:
                providers[key] = {**providers[key], **val}
            else:
                # Custom provider from toml — default to openai-compatible
                providers[key] = {"type": "openai", "name": key.title(), **val}

    # Merge runtime overrides (from dashboard)
    overrides = _load_overrides()
    for key, val in overrides.items():
        if key in providers:
            providers[key] = {**providers[key], **val}
        else:
            providers[key] = {"type": "openai", "name": key.title(), **val}

    # Resolve API keys from environment
    for key, prov in providers.items():
        env_var = prov.get("api_key_env", "")
        # Don't overwrite an explicit api_key with empty env
        if env_var and not prov.get("api_key"):
            prov["api_key"] = os.environ.get(env_var, "")
        # Mark as configured
        prov["configured"] = bool(prov.get("api_key")) or prov.get("type") == "openai" and "localhost" in prov.get("base_url", "")

    return providers


def get_provider_for_model(model: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Given a model name, figure out which provider to use.

    Matching order:
        1. Check if model is listed in a provider's models list
        2. Fall back to prefix matching (gpt-* → openai, claude-* → anthropic, etc.)
        3. Check active_provider in config
        4. Default to openai-compatible
    """
    providers = get_all_providers(config)

    # 1. Exact model match in a provider's model list
    for key, prov in providers.items():
        if model in prov.get("models", []):
            return {**prov, "provider_key": key}

    # 2. Prefix matching
    prefix_map = {
        "gpt": "openai", "o1": "openai", "o3": "openai",
        "claude": "anthropic",
        "grok": "grok",
        "gemini": "gemini",
        "deepseek": "deepseek",
        "mistral": "mistral",
        "llama": "ollama", "codellama": "ollama",
        "glm": "zai",
    }
    for prefix, provider_key in prefix_map.items():
        if model.startswith(prefix) and provider_key in providers:
            return {**providers[provider_key], "provider_key": provider_key}

    # 3. Active provider from config
    active = (config or {}).get("agent", {}).get("active_provider")
    if active and active in providers:
        return {**providers[active], "provider_key": active}

    # 4. Fallback
    return {**providers.get("openai", {}), "provider_key": "openai"}


def set_provider_override(key: str, data: dict[str, Any]) -> None:
    """Update a single provider's runtime config (called from dashboard)."""
    overrides = _load_overrides()
    if key in overrides:
        overrides[key] = {**overrides[key], **data}
    else:
        overrides[key] = data
    _save_overrides(overrides)


def add_custom_provider(key: str, data: dict[str, Any]) -> None:
    """Add a completely new provider (called from dashboard)."""
    data.setdefault("type", "openai")
    data.setdefault("name", key.title())
    data.setdefault("models", [])
    overrides = _load_overrides()
    overrides[key] = data
    _save_overrides(overrides)


def remove_custom_provider(key: str) -> bool:
    """Remove a custom provider. Cannot remove builtins."""
    if key in BUILTIN_PROVIDERS:
        return False
    overrides = _load_overrides()
    if key in overrides:
        del overrides[key]
        _save_overrides(overrides)
        return True
    return False


def set_active_model(config: dict[str, Any], model: str) -> None:
    """Set the active model in config (runtime only)."""
    config.setdefault("agent", {})["default_model"] = model


def get_provider_summary(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return a dashboard-friendly summary of all providers."""
    providers = get_all_providers(config)
    active_model = (config or {}).get("agent", {}).get("default_model", "gpt-4o-mini")

    summary = []
    for key, prov in providers.items():
        is_builtin = key in BUILTIN_PROVIDERS
        summary.append({
            "key": key,
            "name": prov.get("name", key),
            "type": prov.get("type", "openai"),
            "base_url": prov.get("base_url", ""),
            "api_key_env": prov.get("api_key_env", ""),
            "has_key": bool(prov.get("api_key")),
            "configured": prov.get("configured", False),
            "models": prov.get("models", []),
            "builtin": is_builtin,
            "active": active_model in prov.get("models", []),
        })
    return summary
