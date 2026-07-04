"""Single source of truth for provider onboarding metadata.

Before Sprint 2 (2026-07-04 audit) THREE tables answered "which
providers exist, which env var, which default model": quickstart's
``KEY_PATTERNS``/``PROVIDER_MENU``, setup_wizard's ``PROVIDERS``/
``MODEL_OPTIONS``, and gateway/src/providers.ts — and they disagreed
(the wizards still recommended claude-3-5-sonnet-latest while the
catalog and gateway had moved to the 4.x line).

Python consumers derive from THIS module. The TypeScript table in
``gateway/src/providers.ts`` can't import it, so
``tests/test_provider_table_consistency.py`` parses the TS file and
fails the suite when the two drift. Default models should track
``windyfly.agent.models_catalog`` for Anthropic and the gateway's
model lists for the rest.
"""

from __future__ import annotations

from typing import Any

# Ordered: more specific key prefixes first (sk-ant- before sk-).
PROVIDER_DEFAULTS: list[dict[str, Any]] = [
    {
        "name": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
        "default_model": "claude-sonnet-4-6",
        "budget_model": "claude-haiku-4-5",
        "premium_model": "claude-opus-4-7",
        "models": "claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5",
        "hint": "Starts with sk-ant-...",
        "url": "https://console.anthropic.com/settings/keys",
    },
    {
        "name": "xAI Grok",
        "env_var": "GROK_API_KEY",
        "key_prefix": "xai-",
        "default_model": "grok-3-mini",
        "budget_model": "grok-3-mini",
        "premium_model": "grok-3",
        "models": "grok-3, grok-3-mini",
        "hint": "Starts with xai-...",
        "url": "https://console.x.ai",
    },
    {
        "name": "Google Gemini",
        "env_var": "GEMINI_API_KEY",
        "key_prefix": "AIza",
        "default_model": "gemini-2.5-flash",
        "budget_model": "gemini-2.5-flash",
        "premium_model": "gemini-2.5-pro",
        "models": "gemini-2.5-pro, gemini-2.5-flash",
        "hint": "Starts with AIza...",
        "url": "https://aistudio.google.com/apikey",
    },
    {
        "name": "DeepSeek",
        "env_var": "DEEPSEEK_API_KEY",
        # DeepSeek keys are sk-... like OpenAI; dsk- catches the rare
        # explicit form, otherwise users pick DeepSeek from the menu.
        "key_prefix": "dsk-",
        "default_model": "deepseek-chat",
        "budget_model": "deepseek-chat",
        "premium_model": None,
        "models": "deepseek-chat, deepseek-reasoner",
        "hint": "Starts with sk-...",
        "url": "https://platform.deepseek.com",
    },
    {
        "name": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        # Catch-all LAST: sk- also matches Anthropic/DeepSeek keys, so
        # every more-specific prefix above must be checked first.
        "key_prefix": "sk-",
        "default_model": "gpt-4o-mini",
        "budget_model": "gpt-4o-mini",
        "premium_model": "gpt-4o",
        "models": "gpt-4o, gpt-4o-mini, o3-mini",
        "hint": "Starts with sk-...",
        "url": "https://platform.openai.com/api-keys",
    },
    {
        "name": "Mistral",
        "env_var": "MISTRAL_API_KEY",
        "key_prefix": None,  # no reliable prefix — menu-only
        "default_model": "mistral-large-latest",
        "budget_model": "mistral-small-latest",
        "premium_model": None,
        "models": "mistral-large-latest, mistral-small-latest",
        "hint": "From console.mistral.ai",
        "url": "https://console.mistral.ai/api-keys",
    },
]


def key_detection_order() -> list[dict[str, Any]]:
    """Providers with a key prefix, in safe most-specific-first order."""
    return [p for p in PROVIDER_DEFAULTS if p["key_prefix"]]


def by_name(name: str) -> dict[str, Any] | None:
    for p in PROVIDER_DEFAULTS:
        if p["name"].lower() == name.lower():
            return p
    return None
