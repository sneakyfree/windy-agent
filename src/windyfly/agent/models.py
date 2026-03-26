"""LLM provider abstraction.

Supports OpenAI (gpt-*) and Anthropic (claude-*) models with
automatic routing, retry logic, and cost estimation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Cost per 1K tokens (update as prices change)
COST_MAP: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "claude-sonnet": {"input": 0.003, "output": 0.015},
    "claude-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.00025, "output": 0.00125},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost for a given model and token counts.

    Args:
        model: Model name.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Estimated cost in USD.
    """
    # Find best matching cost map entry
    costs = COST_MAP.get(model)
    if not costs:
        # Try prefix matching
        for key in COST_MAP:
            if model.startswith(key):
                costs = COST_MAP[key]
                break
    if not costs:
        # Default to gpt-4o-mini pricing
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
    """Call an LLM provider (OpenAI or Anthropic) based on the model name.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        model: Model name. Defaults to config default or 'gpt-4o-mini'.
        temperature: Sampling temperature.
        max_tokens: Max response tokens.
        tools: Optional tool schemas for function calling.
        config: Optional config dict for defaults.

    Returns:
        Dict with: content, model, input_tokens, output_tokens, tool_calls.

    Raises:
        RuntimeError: If all retries are exhausted.
    """
    if model is None:
        model = (config or {}).get("agent", {}).get("default_model", "gpt-4o-mini")

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            if model.startswith(("gpt", "o1", "o3")):
                return _call_openai(messages, model, temperature, max_tokens, tools)
            elif model.startswith("claude"):
                return _call_anthropic(messages, model, temperature, max_tokens, tools)
            else:
                # Default to OpenAI-compatible
                return _call_openai(messages, model, temperature, max_tokens, tools)
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
) -> dict[str, Any]:
    """Call OpenAI ChatCompletion API."""
    import openai

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    client = openai.OpenAI()
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
