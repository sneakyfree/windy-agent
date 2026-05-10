"""LLM provider abstraction.

Routes to any provider via the provider registry. Anthropic uses its own
SDK (different API format). Everything else goes through the OpenAI SDK
with a swappable base_url — works for Grok, Gemini, DeepSeek, Mistral,
Ollama, and any future OpenAI-compatible lab.

Resilience: when ``[agent] failover_chain`` is set in config, ``call_llm``
walks the chain on provider failures (5xx, 429, 401, network) and skips
providers in cooldown after recent failures (circuit-breaker pattern).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
    "glm-4.7": {"input": 0.0006, "output": 0.0023},
    "glm-4": {"input": 0.0014, "output": 0.0014},
    "glm-4-flash": {"input": 0.0, "output": 0.0},
}

# Per-provider circuit breaker. After N consecutive failures, skip the
# provider for an exponentially-growing cooldown window so we don't burn
# 30 doomed calls per minute against a 503-ing endpoint.
_COOLDOWN_BASE_S = 30
_COOLDOWN_MAX_S = 300
# provider_key → (cooldown_until_ts, consecutive_failure_count)
_provider_cooldowns: dict[str, tuple[float, int]] = {}


def _is_provider_in_cooldown(provider_key: str) -> bool:
    entry = _provider_cooldowns.get(provider_key)
    if entry is None:
        return False
    cooldown_until, _ = entry
    return time.time() < cooldown_until


def _record_provider_failure(provider_key: str) -> None:
    _, prev_count = _provider_cooldowns.get(provider_key, (0.0, 0))
    new_count = prev_count + 1
    cooldown_s = min(_COOLDOWN_BASE_S * new_count, _COOLDOWN_MAX_S)
    _provider_cooldowns[provider_key] = (time.time() + cooldown_s, new_count)
    logger.warning(
        "Provider %s in cooldown for %ds (consecutive failures: %d)",
        provider_key, cooldown_s, new_count,
    )


def _record_provider_success(provider_key: str) -> None:
    if provider_key in _provider_cooldowns:
        del _provider_cooldowns[provider_key]


def _build_chain(
    explicit_model: str | None,
    config: dict[str, Any] | None,
) -> list[str]:
    """Decide which models to walk on this call.

    If the caller passed an explicit ``model``, honor it as the only target
    (no failover — they asked for *this* model specifically). Otherwise use
    the configured ``failover_chain``, falling back to the single
    ``default_model`` for back-compat with existing configs.
    """
    if explicit_model is not None:
        return [explicit_model]
    agent_cfg = (config or {}).get("agent", {})
    chain = agent_cfg.get("failover_chain")
    if chain:
        return list(chain)
    return [agent_cfg.get("default_model", "gpt-4o-mini")]


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
    """Call an LLM, walking the configured failover chain on failures.

    Per provider in the chain:
      - Skip if currently in cooldown.
      - Skip if no api_key configured (and not a localhost provider).
      - One attempt, immediate fail-forward — the per-provider cooldown
        plus the chain ordering already buys us recovery without burning
        triple retries against a dying endpoint.

    On success, the provider's cooldown is cleared. On final failure
    after the whole chain, raise with the list of attempted providers.

    When ``model`` is passed explicitly, the chain is not used (the
    caller asked for that specific model).
    """
    chain = _build_chain(model, config)

    last_error: Exception | None = None
    attempted: list[str] = []
    skipped: list[str] = []

    for chain_model in chain:
        provider = get_provider_for_model(chain_model, config)
        provider_key = provider.get("provider_key", "unknown")
        provider_type = provider.get("type", "openai")
        api_key = provider.get("api_key", "")
        base_url = provider.get("base_url", "https://api.openai.com/v1")

        # Skip if no key (Ollama-style local providers don't need one)
        if not api_key and "localhost" not in base_url:
            skipped.append(f"{provider_key}({chain_model}):no-key")
            continue

        # Skip if in cooldown — unless this is the only chain entry, in
        # which case attempt anyway as a degraded last resort
        if _is_provider_in_cooldown(provider_key) and len(chain) > 1:
            skipped.append(f"{provider_key}({chain_model}):cooldown")
            continue

        attempted.append(f"{provider_key}({chain_model})")
        is_failover = len(attempted) > 1
        if is_failover:
            logger.warning(
                "FAILOVER: %s after %s",
                attempted[-1], ", ".join(attempted[:-1]),
            )

        try:
            if provider_type == "anthropic":
                result = _call_anthropic(
                    messages, chain_model, temperature, max_tokens, tools, api_key,
                )
            else:
                result = _call_openai(
                    messages, chain_model, temperature, max_tokens,
                    tools, base_url, api_key,
                )
            _record_provider_success(provider_key)
            return result
        except Exception as e:
            last_error = e
            logger.warning(
                "Provider %s (%s) failed: %s",
                provider_key, chain_model, e,
            )
            _record_provider_failure(provider_key)

    summary = f"attempted={attempted}"
    if skipped:
        summary += f", skipped={skipped}"
    raise RuntimeError(
        f"LLM call failed across all providers in chain ({summary}): {last_error}"
    )


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
        # type=function is REQUIRED on the round-trip per OpenAI spec.
        # OpenAI's own API tolerates omission; Z.AI's GLM-4.x compatibility
        # layer rejects with 400 1214 ("工具类型不能为空" / "tool type cannot
        # be empty") on the second call when the assistant message's
        # tool_calls lack it. Including type=function unconditionally so
        # any provider's strictness is satisfied.
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
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
        # Uniform shape with _call_anthropic — citations / server-
        # tool tracking are Anthropic-only today (PR #164). Non-
        # Anthropic providers return empty defaults so the agent
        # loop doesn't have to branch on provider.
        "citations": [],
        "server_tools_used": 0,
    }


# Anthropic tool names must match ``^[a-zA-Z0-9_-]{1,128}$``. Three
# classes of failure happen in practice:
#   1. Dots — our static capability ids use them (``fs.read_file``,
#      ``agent.create_collaborator``, ``shell.exec``); kept as a
#      first-class case for back-compat with the legacy ``__W_DOT__``
#      marker so any in-flight tool call round-trips.
#   2. Other illegal chars — dynamic tool names (e.g., a collaborator
#      named "Math Helper" produced ``collaborator.Math Helper.send``)
#      contain spaces, slashes, colons, unicode, anything user-supplied.
#      The original sanitizer only handled (1), so every Anthropic call
#      with such a tool returned 400. Production log evidence:
#        ``tools.25.custom.name: String should match pattern``.
#   3. Length > 128 — defense-in-depth for pathological cases.
#
# Strategy:
#   - Step 1: ``.`` → ``__W_DOT__`` (preserves legacy round-trip).
#   - Step 2: any remaining illegal char → ``_xNNNNNN_`` (6-digit
#     lowercase hex codepoint; covers all of unicode through 0x10FFFF).
#     The ``_x...._`` envelope can't collide with the legacy marker
#     (``__W_DOT__`` has uppercase letters; hex pattern is lowercase).
#   - Step 3: if final length > 128, truncate to 119 + ``_`` + 8-char
#     SHA-256 hash of the *original* name for uniqueness. Truncated
#     names are NOT losslessly restorable; ``_restore_from_anthropic``
#     returns the truncated form and the caller's tool registry will
#     emit "unknown tool" — preferable to silently routing to the
#     wrong tool. In practice no real tool name approaches 128 chars.
#   - Empty input falls back to a stable placeholder so we never emit
#     an empty tool name (which would also fail the regex).

_ANTHROPIC_DOT_MARKER = "__W_DOT__"
_ANTHROPIC_INVALID_CHAR = re.compile(r"[^a-zA-Z0-9_-]")
_ANTHROPIC_HEX_PATTERN = re.compile(r"_x([0-9a-f]{6})_")
_ANTHROPIC_MAX_NAME_LEN = 128
_ANTHROPIC_EMPTY_FALLBACK = "unnamed_tool"


def _sanitize_for_anthropic(name: str) -> str:
    """Make ``name`` valid against Anthropic's tool-name regex.

    Round-trippable via :func:`_restore_from_anthropic` for any input
    whose sanitized form fits in 128 chars (every real tool name).
    """
    if not name:
        return _ANTHROPIC_EMPTY_FALLBACK
    out = name.replace(".", _ANTHROPIC_DOT_MARKER)
    out = _ANTHROPIC_INVALID_CHAR.sub(
        lambda m: f"_x{ord(m.group(0)):06x}_", out
    )
    if len(out) > _ANTHROPIC_MAX_NAME_LEN:
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        # Reserve 9 chars for "_" + 8-char hash suffix.
        keep = _ANTHROPIC_MAX_NAME_LEN - 9
        out = out[:keep] + "_" + suffix
        logger.warning(
            "Tool name truncated for Anthropic (orig_len=%d): %r → %r",
            len(name), name[:60] + "..." if len(name) > 60 else name, out,
        )
    return out


def _restore_from_anthropic(name: str) -> str:
    """Inverse of :func:`_sanitize_for_anthropic` for non-truncated names."""
    out = _ANTHROPIC_HEX_PATTERN.sub(
        lambda m: chr(int(m.group(1), 16)), name
    )
    return out.replace(_ANTHROPIC_DOT_MARKER, ".")


def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Translate OpenAI-format tool schemas to Anthropic format.

    OpenAI: ``{"type": "function", "function": {"name", "description", "parameters"}}``
    Anthropic: ``{"name", "description", "input_schema"}``

    The capability registry emits OpenAI shape (so GLM/Grok/etc. accept
    it natively); when the chain hops to Anthropic we translate on the
    way out so callers don't need to know the active provider. Tool
    names are sanitized — see ``_ANTHROPIC_DOT_MARKER``.

    Server-side tools (PR #164) — ``web_search_20250305`` and
    similar — are PASSED THROUGH UNCHANGED. Anthropic recognizes
    them by their ``type`` field directly; they don't need the
    OpenAI-shape ``function`` wrapper, and the input_schema is
    implicit. Detection: any tool whose ``type`` is something
    OTHER than "function" is treated as a server-side tool.
    """
    out = []
    for t in tools:
        # Server-side tool? Anthropic native types like
        # web_search_20250305 / code_execution / computer go
        # through unchanged.
        if t.get("type") and t.get("type") != "function":
            out.append(dict(t))
            continue
        fn = t.get("function") or t
        out.append({
            "name": _sanitize_for_anthropic(fn["name"]),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or fn.get("input_schema") or {
                "type": "object",
                "properties": {},
            },
        })
    return out


def _openai_messages_to_anthropic(
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """Translate OpenAI-format chat messages to Anthropic format.

    Returns ``(system_text, api_messages)``. The agent loop builds
    OpenAI-shaped ``tool_calls``/``tool`` round-trip messages because
    that's what every provider except Anthropic accepts directly. Here
    we fold them into Anthropic's ``tool_use``/``tool_result`` content
    blocks so the round-trip works without the loop knowing which
    provider it talks to.

    System messages are concatenated and returned separately (Anthropic
    takes ``system`` as a top-level kwarg, not in ``messages``).
    """
    system_parts: list[str] = []
    api_messages: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            content = msg.get("content") or ""
            if content:
                system_parts.append(content)
            continue

        if role == "tool":
            # OpenAI tool result → Anthropic user message with
            # tool_result content block. Anthropic groups consecutive
            # tool_results in one user message; merging keeps message
            # count low without changing semantics.
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            if api_messages and api_messages[-1].get("role") == "user" \
                    and isinstance(api_messages[-1].get("content"), list):
                api_messages[-1]["content"].append(block)
            else:
                api_messages.append({"role": "user", "content": [block]})
            continue

        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = []
            text = msg.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args else {}
                    except json.JSONDecodeError:
                        args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": _sanitize_for_anthropic(fn.get("name", "")),
                    "input": args or {},
                })
            api_messages.append({"role": "assistant", "content": blocks})
            continue

        # Plain user/assistant text message — pass through.
        api_messages.append({
            "role": role,
            "content": msg.get("content", ""),
        })

    return ("\n".join(system_parts).strip(), api_messages)


def _call_anthropic(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
    api_key: str = "",
) -> dict[str, Any]:
    """Call Anthropic Messages API.

    Translates OpenAI-shaped messages and tool schemas to Anthropic's
    native format on the way out, and Anthropic's tool_use blocks back
    to OpenAI-shaped tool_calls on the way in. Callers stay in
    OpenAI-shape and the rest of the codebase doesn't have to branch.
    """
    import anthropic

    system_text, api_messages = _openai_messages_to_anthropic(messages)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_text:
        kwargs["system"] = system_text
    if tools:
        kwargs["tools"] = _openai_tools_to_anthropic(tools)

    # Use OAuth token if available, else explicit key, else env default.
    # ``sk-ant-oat01-`` keys are Claude.ai OAuth access tokens — they
    # must go on ``Authorization: Bearer …`` with the OAuth beta header,
    # NOT on ``x-api-key`` (Anthropic 429s the latter with no detail).
    # ``sk-ant-api…`` keys take the normal x-api-key path.
    #
    # Additionally: OAuth tokens only bill against the user's Claude Pro
    # subscription when the first system message identifies the request
    # as Claude Code. Without this prefix, every request 429s with an
    # empty error body — the same gate Claude Code itself satisfies.
    # We prepend the identifier transparently; the rest of the assembled
    # system prompt (Windy Fly personality, capability hints, etc.)
    # follows after a separator and steers the model's behavior.
    from windyfly.agent.oauth import get_oauth_manager

    oauth = get_oauth_manager()
    oauth_token = oauth.access_token if oauth else (
        api_key if api_key.startswith("sk-ant-oat01-") else ""
    )
    if oauth_token:
        client = anthropic.Anthropic(
            auth_token=oauth_token,
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        # Anthropic's OAuth gate is strict: the first system content
        # block must be EXACTLY the Claude Code identifier — not even a
        # trailing newline of extra text passes. Concatenating Windy's
        # personality after the identifier 429s. The accepted shape is
        # a two-block ``system`` array: block 0 is the bare identifier
        # (passes gate), block 1 carries everything else.
        cc_id = "You are Claude Code, Anthropic's official CLI for Claude."
        existing_system = kwargs.get("system", "")
        if existing_system:
            kwargs["system"] = [
                {"type": "text", "text": cc_id},
                {"type": "text", "text": existing_system},
            ]
        else:
            kwargs["system"] = cc_id
    elif api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic()
    response = client.messages.create(**kwargs)

    content = ""
    tool_calls = None
    citations: list[dict[str, Any]] = []
    server_tools_used = 0
    for block in response.content:
        if block.type == "text":
            content += block.text
            # Server-side web_search (PR #164) attaches citation
            # metadata to text blocks. Harvest into a flat list
            # so the agent loop can render a "Sources:" footer.
            block_citations = getattr(block, "citations", None) or []
            for c in block_citations:
                # Anthropic SDK objects → dict for downstream uniformity.
                if hasattr(c, "model_dump"):
                    citations.append(c.model_dump())
                elif isinstance(c, dict):
                    citations.append(c)
                else:
                    # Last-ditch: pull the common attrs we care about.
                    citations.append({
                        "url": getattr(c, "url", None),
                        "title": getattr(c, "title", None),
                        "cited_text": getattr(c, "cited_text", None),
                    })
        elif block.type == "tool_use":
            if tool_calls is None:
                tool_calls = []
            # Serialize input dict to JSON string so the rest of the
            # loop (which assumes OpenAI shape) can json.loads() it
            # uniformly. Anthropic returns input as a parsed dict;
            # OpenAI returns it as a JSON string.
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": _restore_from_anthropic(block.name),
                    "arguments": json.dumps(block.input or {}),
                },
            })
        elif block.type in ("server_tool_use", "web_search_tool_result"):
            # Server-side tools (PR #164: web_search_20250305 et al.)
            # produce informational trace blocks. The search HAS
            # ALREADY RUN on Anthropic's side; we MUST NOT add these
            # to tool_calls (would trigger a client-side dispatch
            # for a tool we don't have registered). Just count them
            # so the daily-cap counter can bump.
            if block.type == "server_tool_use":
                server_tools_used += 1
        # Unknown block types: silently skip — Anthropic adds new
        # block types (thinking, redacted_thinking, etc.) over time
        # and we don't want a new block type to crash the parser.

    return {
        "content": content,
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "tool_calls": tool_calls,
        "citations": citations,
        "server_tools_used": server_tools_used,
    }
