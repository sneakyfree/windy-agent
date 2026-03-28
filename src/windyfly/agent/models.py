"""LLM provider abstraction.

Routes to any provider via the provider registry. Anthropic uses its own
SDK (different API format). Everything else goes through the OpenAI SDK
with a swappable base_url — works for Grok, Gemini, DeepSeek, Mistral,
Ollama, and any future OpenAI-compatible lab.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from windyfly.agent.providers import get_provider_for_model

logger = logging.getLogger(__name__)

# Cost per 1K tokens (update as prices change)
COST_MAP: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "claude-sonnet": {"input": 0.003, "output": 0.015},
    "claude-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.00025, "output": 0.00125},
    "grok-3": {"input": 0.003, "output": 0.015},
    "grok-3-mini": {"input": 0.0003, "output": 0.0005},
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.01},
    "gemini-2.5-flash": {"input": 0.00015, "output": 0.0006},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost for a given model and token counts."""
    costs = COST_MAP.get(model)
    if not costs:
        for key in COST_MAP:
            if model.startswith(key):
                costs = COST_MAP[key]
                break
    if not costs:
        costs = COST_MAP["gpt-4o-mini"]

    return (input_tokens / 1000) * costs["input"] + (output_tokens / 1000) * costs["output"]


def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    tools: list[dict] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call an LLM provider based on the model name.

    Automatically routes to the correct provider using the registry.
    Anthropic uses its own SDK; everything else uses the OpenAI SDK
    with a provider-specific base_url.
    """
    if model is None:
        model = (config or {}).get("agent", {}).get("default_model", "gpt-4o-mini")

    provider = get_provider_for_model(model, config)
    provider_type = provider.get("type", "openai")
    api_key = provider.get("api_key", "")

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            if provider_type == "anthropic":
                return _call_anthropic(messages, model, temperature, max_tokens, tools, api_key)
            else:
                base_url = provider.get("base_url", "https://api.openai.com/v1")
                return _call_openai(messages, model, temperature, max_tokens, tools, base_url, api_key)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, max_retries, wait, e,
                )
                time.sleep(wait)

    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}")


def _call_openai(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "",
) -> dict[str, Any]:
    """Call any OpenAI-compatible API (OpenAI, Grok, Gemini, DeepSeek, etc.)."""
    import openai

    client_kwargs: dict[str, Any] = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    if api_key:
        client_kwargs["api_key"] = api_key

    # For local providers (Ollama) that don't need a key
    if not api_key and "localhost" in base_url:
        client_kwargs["api_key"] = "ollama"

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    client = openai.OpenAI(**client_kwargs)
    response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    usage = response.usage

    tool_calls = None
    if choice.message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in choice.message.tool_calls
        ]

    return {
        "content": choice.message.content or "",
        "model": model,
        "input_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
        "tool_calls": tool_calls,
    }


def _call_anthropic(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
    api_key: str = "",
) -> dict[str, Any]:
    """Call Anthropic Messages API."""
    import anthropic

    # Anthropic uses separate system message
    system_text = ""
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text += msg["content"] + "\n"
        else:
            api_messages.append(msg)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_text.strip():
        kwargs["system"] = system_text.strip()
    if tools:
        kwargs["tools"] = tools

    # Use OAuth token if available, else explicit key, else env default
    from windyfly.agent.oauth import get_oauth_manager

    oauth = get_oauth_manager()
    if oauth:
        client = anthropic.Anthropic(api_key=oauth.access_token)
    elif api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic()
    response = client.messages.create(**kwargs)

    content = ""
    tool_calls = None
    for block in response.content:
        if block.type == "text":
            content += block.text
        elif block.type == "tool_use":
            if tool_calls is None:
                tool_calls = []
            tool_calls.append({
                "id": block.id,
                "function": {
                    "name": block.name,
                    "arguments": block.input,
                },
            })

    return {
        "content": content,
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "tool_calls": tool_calls,
    }
