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
import os
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
# Cooldown to apply on permanent-auth errors (401 invalid x-api-key,
# 403 permission denied, credit-balance-too-low). The cooldown
# escalator is meant for *transient* failures that recover on their
# own — a permanent-auth failure won't fix itself this process
# lifetime, so a long cooldown short-circuits 119 doomed calls
# per hour. Companion to PR #209's auto-resurrect short-circuit:
# both layers now agree that perma-auth ≠ transient.
_COOLDOWN_AUTH_DEAD_S = 60 * 60  # 1 hour
# provider_key → (cooldown_until_ts, consecutive_failure_count)
_provider_cooldowns: dict[str, tuple[float, int]] = {}


def _cooldown_state_path():
    from windyfly.platform import windy_state_dir
    return windy_state_dir() / "provider-cooldowns.json"


def _save_cooldowns() -> None:
    """Persist the circuit breaker across restarts (best-effort, atomic).

    Pre-fix the breaker was a module dict: every restart — including
    panic-triggered and watchdog-kill restarts — zeroed all cooldown
    history, so a crash-restart loop hammered a dead provider afresh
    each cycle and a 1h perma-auth cooldown never survived the very
    restart the auth failure tends to cause.
    """
    try:
        path = _cooldown_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_provider_cooldowns))
        tmp.replace(path)
    except OSError as e:
        logger.debug("Could not persist provider cooldowns: %s", e)


def _load_cooldowns() -> None:
    """Restore unexpired cooldowns at import; drop stale entries."""
    try:
        raw = json.loads(_cooldown_state_path().read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return
    now = time.time()
    for provider, entry in raw.items():
        try:
            until, count = float(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if until > now:
            _provider_cooldowns[provider] = (until, count)


_load_cooldowns()


def _is_provider_in_cooldown(provider_key: str) -> bool:
    entry = _provider_cooldowns.get(provider_key)
    if entry is None:
        return False
    cooldown_until, _ = entry
    return time.time() < cooldown_until


def _record_provider_failure(
    provider_key: str,
    error_str: str | None = None,
) -> None:
    """Record a provider failure + set a cooldown window. When
    ``error_str`` is classified as permanent-auth (matches PR
    #209's ``is_permanent_auth_error``), use a long 1-hour
    cooldown so the chain skips the doomed provider until the
    operator refreshes credentials. Transient failures keep the
    exponential 30s→300s escalator they had pre-PR.
    """
    _, prev_count = _provider_cooldowns.get(provider_key, (0.0, 0))
    new_count = prev_count + 1
    # Lazy import to avoid a circular import (resurrect imports
    # models indirectly through the agent loop in some test paths).
    try:
        from windyfly.agent.resurrect import is_permanent_auth_error
        is_perma_auth = is_permanent_auth_error(error_str)
    except Exception:
        is_perma_auth = False
    if is_perma_auth:
        cooldown_s = _COOLDOWN_AUTH_DEAD_S
    else:
        cooldown_s = min(_COOLDOWN_BASE_S * new_count, _COOLDOWN_MAX_S)
    _provider_cooldowns[provider_key] = (time.time() + cooldown_s, new_count)
    _save_cooldowns()
    logger.warning(
        "Provider %s in cooldown for %ds (consecutive failures: %d%s)",
        provider_key, cooldown_s, new_count,
        " — permanent auth failure, long cooldown" if is_perma_auth else "",
    )


def _record_provider_success(provider_key: str) -> None:
    if provider_key in _provider_cooldowns:
        del _provider_cooldowns[provider_key]
        _save_cooldowns()


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


def _max_oauth_active() -> bool:
    """Per ADR-022 exception register: when Grant's Anthropic Max OAuth
    token is active, allow direct Anthropic SDK calls. Routing through
    Mind would force pay-per-call api03 billing instead of the Max sub.
    All non-Max-OAuth LLM calls MUST route through Mind per ADR-010 §8.

    Two detection paths, mirroring ``_call_anthropic`` at line ~590:

    1. Explicit OAuth env vars handled by ``OAuthManager``
       (``ANTHROPIC_OAUTH_ACCESS_TOKEN`` / refresh / expires_at).
    2. Fallback: oat token stuffed directly in ``ANTHROPIC_API_KEY``.
       Most installers and quickstart paths default to the latter —
       there's no separate prompt for an "OAuth token" vs an "API key",
       so the user pastes whatever they have into the one creds field
       and the prefix tells us which path to use.

    Without (2), ``_max_oauth_active()`` returned False whenever an
    operator put the oat token in ``ANTHROPIC_API_KEY``, and the chain
    routed through Mind on every call. Today Mind happens to be
    unreachable so the chain falls through to the direct Anthropic
    provider (which DOES detect the prefix) — but that's an accident
    of infrastructure state, not a design property. The moment Mind
    comes online with Anthropic models registered, Max-plan users
    silently start paying API03 billing.

    Surfaced 2026-05-17 while diagnosing windy-0 — Grant's env had the
    oat token in ``ANTHROPIC_API_KEY``, the direct call site honored
    it (Max plan billing), but this gate didn't, so the Mind bypass
    was a coin flip on whether Mind was up that minute.
    """
    try:
        from windyfly.agent.oauth import get_oauth_manager
        oauth = get_oauth_manager()
        if oauth and getattr(oauth, "access_token", None):
            return True
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return api_key.startswith("sk-ant-oat01-")
    except Exception:
        return False


def supports_cap(model_id: str, cap: int) -> tuple[bool, str | None]:
    """Re-export of ``models_catalog.supports_cap`` so call sites that
    already import from ``models`` don't need a second import."""
    from windyfly.agent.models_catalog import supports_cap as _sc
    return _sc(model_id, cap)


def _fingerprint_token(token: str) -> str:
    """Return a redacted token identifier safe to print to chat / logs.

    Format: ``{first 15 chars}…{last 4 chars}`` — e.g. for an oat token
    that becomes ``sk-ant-oat01-Vw…wAAA``. Enough for an operator with
    multiple credentials to identify which one is live at a glance,
    without spilling the body anywhere.

    Returns ``"(empty)"`` for an empty / too-short input rather than
    leaking truncated low-entropy bytes.

    Surfaced 2026-05-19 when Grant asked /status to disambiguate
    OAuth tokens — pre-fix /status just said "OAuth Max" with no
    fingerprint, so multi-token setups had no way to tell which.
    """
    if not token or len(token) < 20:
        return "(empty)"
    return f"{token[:15]}…{token[-4:]}"


# Per-model context-window caps. Used by /status and the
# LOW WORKING MEMORY block in prompt.py.
#
# Anthropic's pre-2026 default was 200K. Opus 4.7 shipped with 1M as
# the default; Sonnet 4.6 + Haiku 4.5 require the ``context-1m-
# 2025-08-07`` beta header to access 1M (the bot does NOT currently
# enable this beta, so they stay at 200K in practice). Stay
# accurate per model — overstating the cap makes pct_remaining lie
# in the user's favor (e.g., "94% free" when actually 28% free).
#
# Keep this list in sync with the model_versions config; when the
# config gains a new model, add it here too.
_MODEL_CONTEXT_CAPS: dict[str, int] = {
    # Anthropic
    "claude-opus-4-7":            1_000_000,
    "claude-opus-4-7[1m]":        1_000_000,
    "claude-sonnet-4-6":          200_000,
    "claude-sonnet-4-6-20251022": 200_000,
    "claude-haiku-4-5":           200_000,
    "claude-haiku-4-5-20251001":  200_000,
    # OpenAI
    "gpt-4o":                     128_000,
    "gpt-4o-mini":                128_000,
    "o1":                         200_000,
    "o3":                         200_000,
    # Ollama (the most common local fallbacks)
    "llama3.2:3b":                 8_192,
    "llama3.1:8b":               128_000,
}


def get_context_cap(model: str) -> int:
    """Return the model's context-window size in tokens.

    Falls back to a conservative 200K for unknown Claude models and
    to 8K for everything else (small-model assumption). Heuristic
    fallback so an unrecognized variant doesn't make /status crash.
    """
    if not model:
        return 200_000
    if model in _MODEL_CONTEXT_CAPS:
        return _MODEL_CONTEXT_CAPS[model]
    low = model.lower()
    if "opus" in low:
        return 1_000_000
    if "sonnet" in low or "haiku" in low:
        return 200_000
    if "gpt" in low or low.startswith("o1") or low.startswith("o3"):
        return 128_000
    return 8_192


def get_anthropic_auth_path() -> dict[str, str]:
    """Return labeled facts about the active Anthropic auth path.

    Sibling of ``_max_oauth_active`` — this one exposes the same
    detection in human-readable form for /status, prompt RUNTIME
    CONTEXT injection, and any other surface that wants to tell the
    user (or the bot itself) "am I on Max plan?"

    Returns a dict with two strings:
      - ``kind``: machine-readable enum (oauth_manager / oauth_api_key
        / api_key / none)
      - ``label_short``: 1-line label for /status ("OAuth Max",
        "OAuth Max (via API_KEY)", "API key (pay-per-token)", "none")
      - ``label_long``: full sentence for the prompt ("OAuth Max plan
        — flat-rate subscription billing") so the bot can quote
        verbatim when asked

    Read-only / no side effects; safe to call once per turn during
    prompt assembly.
    """
    try:
        from windyfly.agent.oauth import get_oauth_manager
        oauth = get_oauth_manager()
        if oauth and getattr(oauth, "access_token", None):
            return {
                "kind": "oauth_manager",
                "label_short": "OAuth Max",
                "label_long": (
                    "OAuth Max plan via OAuthManager — flat-rate "
                    "subscription billing, not pay-per-token"
                ),
                "fingerprint": _fingerprint_token(oauth.access_token),
            }
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key.startswith("sk-ant-oat01-"):
            return {
                "kind": "oauth_api_key",
                "label_short": "OAuth Max (via API_KEY)",
                "label_long": (
                    "OAuth Max plan via ANTHROPIC_API_KEY env "
                    "(sk-ant-oat01- prefix) — flat-rate subscription "
                    "billing, not pay-per-token"
                ),
                "fingerprint": _fingerprint_token(api_key),
            }
        if api_key:
            return {
                "kind": "api_key",
                "label_short": "API key (pay-per-token)",
                "label_long": (
                    "Regular API key (ANTHROPIC_API_KEY, sk-ant-api…) "
                    "— pay-per-token billing against Anthropic Console"
                ),
                "fingerprint": _fingerprint_token(api_key),
            }
        return {
            "kind": "none",
            "label_short": "no auth",
            "label_long": "no Anthropic credentials in env",
            "fingerprint": "(empty)",
        }
    except Exception:
        return {
            "kind": "unknown",
            "label_short": "unknown",
            "label_long": "unable to determine Anthropic auth path",
            "fingerprint": "(empty)",
        }


_ANTHROPIC_AUTH_PATH_LOGGED = False


def _log_anthropic_auth_path_once(
    *, oauth_via_manager: bool, oauth_via_api_key: bool, api_key_only: bool,
) -> None:
    """First-Anthropic-call telemetry: log which auth path is live.

    Without this, the bot has no way to answer the question "am I on
    Max plan or paying API03 rates?" — which was the literal question
    the user asked over Telegram on 2026-05-17 and the bot couldn't
    answer because it has no env introspection. The httpx log line
    only shows ``POST https://api.anthropic.com/v1/messages 200 OK``
    regardless of which header carried auth.

    Logs at INFO level on the FIRST anthropic call after process
    start, then never again — module-level flag keeps it from
    spamming every request. The three signals carry enough detail
    to disambiguate billing without leaking the token.
    """
    global _ANTHROPIC_AUTH_PATH_LOGGED
    if _ANTHROPIC_AUTH_PATH_LOGGED:
        return
    _ANTHROPIC_AUTH_PATH_LOGGED = True
    if oauth_via_manager:
        logger.info(
            "Anthropic auth path: OAuth Max plan via OAuthManager "
            "(ANTHROPIC_OAUTH_ACCESS_TOKEN env). Billing flows "
            "against your Claude subscription, not pay-per-token."
        )
    elif oauth_via_api_key:
        logger.info(
            "Anthropic auth path: OAuth Max plan via ANTHROPIC_API_KEY "
            "fallback (sk-ant-oat01- prefix detected). Billing flows "
            "against your Claude subscription, not pay-per-token. "
            "Optional hardening: move the token to "
            "ANTHROPIC_OAUTH_ACCESS_TOKEN (+ refresh token / "
            "expires_at) so the OAuthManager handles auto-refresh "
            "when the token nears expiry."
        )
    elif api_key_only:
        logger.warning(
            "Anthropic auth path: API key (pay-per-token). NOT on "
            "Max plan — every call bills against your Anthropic "
            "Console balance at full API rates. If you have a Claude "
            "Max subscription, set ANTHROPIC_API_KEY=sk-ant-oat01-… "
            "(or ANTHROPIC_OAUTH_ACCESS_TOKEN=…) to switch to flat-"
            "rate billing."
        )
    else:
        logger.warning(
            "Anthropic auth path: NO credentials in env. SDK will "
            "use whatever its default discovery finds (~/.config "
            "files, etc.) or raise on the next call."
        )


def _try_mind_broker(
    messages: list[dict[str, str]],
    model: str | None,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
) -> dict[str, Any] | None:
    """Route LLM call through Windy Mind broker per ADR-010 §8 + ADR-022.

    Mind is the intelligence kernel; every Windy LLM call should broker
    through it for cost transparency, observability, fallback, and BYOM
    model choice.

    OPT-IN via two env vars:
      MIND_API_URL — defaults to https://api.windymind.ai
      ETERNITAS_PASSPORT_TOKEN (or ETERNITAS_PASSPORT) — the agent's EPT

    Returns the broker's response on success OR None on any failure —
    caller falls through to the direct-provider chain. Zero regression
    risk: when the agent has no EPT (e.g. pre-hatch boot or test rigs),
    this function is a no-op.

    Skipped entirely when Anthropic Max OAuth is active (ADR-022
    exception register #1).
    """
    import os

    ept = os.environ.get("ETERNITAS_PASSPORT_TOKEN") or os.environ.get(
        "ETERNITAS_PASSPORT"
    )
    if not ept:
        return None  # OPT-IN: only fires when EPT is configured

    # Mind tool-calling shipped + was live-verified 2026-07-04 (windy-mind
    # PR #45: tools[] round-trip through gemini-2.5-flash returned real
    # tool_use). Tools now flow to Mind BY DEFAULT — the 0c2 one-soul
    # drill (2026-07-05) showed the old opt-in latch made every keyless
    # agent skip Mind on essentially every real call (the loop always
    # sends its toolset) and fall to the lifeboat. Set
    # WINDY_MIND_SEND_TOOLS=0 to restore the old skip if Mind's tool
    # path ever regresses.
    if tools and os.environ.get("WINDY_MIND_SEND_TOOLS", "1") == "0":
        return None

    # Circuit breaker: Mind is the PRIMARY brain for keyless agents —
    # give it the same cooldown discipline as direct providers instead
    # of one silent 30s attempt per call.
    if _is_provider_in_cooldown("windy-mind"):
        return None

    mind_url = os.environ.get("MIND_API_URL", "https://api.windymind.ai").rstrip("/")

    body: dict[str, Any] = {
        "messages": messages,
        "max_tokens": min(max_tokens, 8192) if max_tokens else 4096,
    }
    if model:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools

    try:
        import httpx

        resp = httpx.post(
            f"{mind_url}/v1/chat",
            headers={
                "Authorization": f"Bearer {ept}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30.0,
        )
        if resp.status_code != 200:
            _record_provider_failure(
                "windy-mind", f"mind http {resp.status_code}: {resp.text[:120]}",
            )
            logger.warning(
                "Mind broker returned %s; falling through to direct chain",
                resp.status_code,
            )
            return None
        translated = _translate_mind_response(resp)
        if translated is None:
            _record_provider_failure("windy-mind", "mind response shape invalid")
            logger.warning(
                "Mind broker response had no usable content; falling through",
            )
            return None
        _record_provider_success("windy-mind")
        return translated
    except Exception as e:
        _record_provider_failure("windy-mind", str(e))
        logger.warning("Mind broker call failed (%s); falling through", e)
        return None


def _translate_mind_response(resp: Any) -> dict[str, Any] | None:
    """Mind's /v1/chat → windyfly's uniform call_llm result shape.

    Mind returns near-OpenAI JSON: {id, model, choices:[{message:{role,
    content}, finish_reason}], usage:{prompt_tokens, completion_tokens},
    provider}. The agent loop expects {content, input_tokens,
    output_tokens, tool_calls, citations, server_tools_used}. PR #173
    returned Mind's JSON verbatim, so the loop would have KeyError'd the
    first time a real Mind reply came back — the integration was wired
    but never load-bearing (2026-07-04 audit).

    Also accepts the already-flat shape ({"content": ...}) for
    forward-compat and test rigs.
    """
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    usage = data.get("usage") or {}

    # Already-flat shape (legacy rigs / future Mind versions)
    if "content" in data and "choices" not in data:
        return {
            "content": data.get("content") or "",
            "input_tokens": data.get("input_tokens", usage.get("prompt_tokens", 0)),
            "output_tokens": data.get("output_tokens", usage.get("completion_tokens", 0)),
            "tool_calls": data.get("tool_calls"),
            "citations": data.get("citations", []),
            "server_tools_used": data.get("server_tools_used", []),
        }

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return None
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if content is None and not tool_calls:
        return None
    return {
        "content": content or "",
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "tool_calls": tool_calls,
        "citations": [],
        "server_tools_used": [],
        "mind_model": data.get("model"),
        "mind_provider": data.get("provider"),
    }


def mind_broker_status() -> dict[str, Any]:
    """Snapshot for /status: is the Mind brain route configured/healthy?"""
    import os as _os

    ept = _os.environ.get("ETERNITAS_PASSPORT_TOKEN") or _os.environ.get(
        "ETERNITAS_PASSPORT"
    )
    configured = bool(ept) and not _max_oauth_active()
    entry = _provider_cooldowns.get("windy-mind")
    cooling = bool(entry and time.time() < entry[0])
    return {
        "configured": configured,
        "url": _os.environ.get("MIND_API_URL", "https://api.windymind.ai"),
        "in_cooldown": cooling,
        "cooldown_remaining_s": max(0, int(entry[0] - time.time())) if cooling else 0,
    }


def _reload_oauth_token() -> bool:
    """Re-sync ANTHROPIC_API_KEY from Claude Code's auto-refreshed
    credentials. Returns True if the in-process token changed.

    A long-running channel reads the OAuth token from os.environ set once at
    process start. Claude Code rotates the token in
    ~/.claude/.credentials.json (the 15-min sync timer updates the env FILE,
    not this live process), so a mid-run rotation leaves the agent 401ing on
    every message until someone restarts it — a grandma's agent going dark.
    Reloading on an auth failure lets it self-heal. Never raises; a machine
    without those credentials (non-Grant install) just gets False.
    """
    try:
        import json
        from pathlib import Path

        creds = Path.home() / ".claude" / ".credentials.json"
        if not creds.exists():
            return False
        fresh = (
            (json.loads(creds.read_text()).get("claudeAiOauth") or {})
            .get("accessToken", "")
        )
        if fresh and fresh != os.environ.get("ANTHROPIC_API_KEY", ""):
            os.environ["ANTHROPIC_API_KEY"] = fresh
            logger.info(
                "Reloaded rotated OAuth token from credentials (…%s)",
                fresh[-8:],
            )
            return True
    except Exception as e:
        logger.debug("OAuth token reload skipped: %s", e)
    return False


def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    tools: list[dict] | None = None,
    config: dict[str, Any] | None = None,
    session_id: str | None = None,
    reasoning_depth: int | None = None,
) -> dict[str, Any]:
    """Call an LLM, walking the configured failover chain on failures.

    Per ADR-010 §8 + ADR-022 §5: when the agent has an Eternitas passport,
    LLM calls route through Mind FIRST (the intelligence kernel handles
    BYOM model choice, cost transparency, and free-tier fallback). If Mind
    is unavailable or returns non-200, falls through to the direct-provider
    chain below.

    Anthropic Max OAuth (ADR-022 exception register #1) bypasses Mind so
    the Max sub billing path stays intact.

    Direct-provider chain (when Mind path no-ops or fails):
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
    # Mind broker first (BYOM moat per ADR-022). Bypass when Max OAuth
    # is active (ADR-022 exception register #1).
    if not _max_oauth_active():
        mind_resp = _try_mind_broker(messages, model, temperature, max_tokens, tools)
        if mind_resp is not None:
            return mind_resp

    chain = _build_chain(model, config)

    last_error: Exception | None = None
    attempted: list[str] = []
    skipped: list[str] = []
    _oauth_reloaded = False  # one-shot mid-run token reload per call

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
                    messages, chain_model, temperature, max_tokens, tools,
                    api_key, session_id=session_id,
                    reasoning_depth=reasoning_depth,
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
            # Self-heal a mid-run OAuth rotation: a stale-token 401 on the
            # Anthropic path is fixable by reloading the token Claude Code
            # just rotated. Retry this same provider ONCE with the freshest
            # known token before cooling it down and failing forward to the
            # lifeboat. The retry must fire whenever the key that just
            # failed differs from the current env token — not only when
            # _reload_oauth_token() changed the env — because the failed
            # key may be stale relative to an env a previous turn already
            # reloaded (windy-0's double-401 → 1h cooldown, 2026-07-08).
            from windyfly.agent.resurrect import is_permanent_auth_error
            retry_key = ""
            if (
                provider_type == "anthropic"
                and not _oauth_reloaded
                and is_permanent_auth_error(str(e))
            ):
                _reload_oauth_token()
                env_key = os.environ.get("ANTHROPIC_API_KEY", "")
                if env_key and env_key != api_key:
                    retry_key = env_key
            if retry_key:
                _oauth_reloaded = True
                try:
                    result = _call_anthropic(
                        messages, chain_model, temperature, max_tokens, tools,
                        retry_key,
                        session_id=session_id, reasoning_depth=reasoning_depth,
                    )
                    _record_provider_success(provider_key)
                    logger.info(
                        "Recovered after OAuth token reload (%s)", provider_key,
                    )
                    return result
                except Exception as e2:
                    last_error = e2
                    logger.warning(
                        "Provider %s retry after token reload also failed: %s",
                        provider_key, e2,
                    )
            _record_provider_failure(provider_key, error_str=str(last_error))

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


# Opus models that support (and default to) extended thinking. Both
# deprecate plain ``temperature`` in favor of a thinking budget; see
# _call_anthropic for the per-version temperature handling.
_EXTENDED_THINKING_PREFIXES = ("claude-opus-4-7", "claude-opus-4-8")


def _thinking_budget(model: str, reasoning_depth: int | None) -> int:
    """Map the ``reasoning_depth`` slider (0–10, the "ultrathink" dial)
    to an extended-thinking token budget for thinking-capable Opus models.

    Returns 0 (no thinking) for non-thinking models or low depth. ``None``
    means the caller didn't specify → treated as the mid default (5).
    depth 4 → 1024 (Anthropic's floor), depth 10 → ~8192.
    """
    if not any(model.startswith(p) for p in _EXTENDED_THINKING_PREFIXES):
        return 0
    depth = 5 if reasoning_depth is None else max(0, min(10, reasoning_depth))
    if depth < 4:
        return 0
    return int(round(1024 + (depth - 4) * (8192 - 1024) / 6))


def _call_anthropic(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
    api_key: str = "",
    session_id: str | None = None,
    reasoning_depth: int | None = None,
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
    # Extended thinking (Opus 4.7+). The whole 4.7+ Opus line deprecates
    # `temperature` outright — both 4-7 and 4-8 now 400 ("temperature is
    # deprecated for this model" without thinking; "temperature may only
    # be set to 1 when thinking is enabled" with it). So drop it for any
    # thinking-capable Opus regardless of whether we enable a budget.
    # The thinking budget scales with the caller's reasoning_depth (the
    # "ultrathink" slider) and is ADDITIVE to max_tokens so the visible
    # reply keeps its full allowance.
    if any(model.startswith(p) for p in _EXTENDED_THINKING_PREFIXES):
        kwargs.pop("temperature", None)
    thinking_budget = _thinking_budget(model, reasoning_depth)
    if thinking_budget:
        kwargs["max_tokens"] = max_tokens + thinking_budget
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
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
    _log_anthropic_auth_path_once(
        oauth_via_manager=oauth is not None,
        oauth_via_api_key=bool(oauth_token) and oauth is None,
        api_key_only=bool(api_key) and not oauth_token,
    )
    # PR #197 — auto-attach extended-context beta when the caller's
    # requested memory cap exceeds the model's native limit. The
    # session_reset module tracks the user's /memory pick; when it's
    # set to e.g. 1M on Sonnet 4.6, we need this beta or Anthropic
    # truncates back to 200K silently.
    betas = ["oauth-2025-04-20"]
    try:
        from windyfly.agent.session_reset import (
            parse_session_id, get_memory_cap,
        )
        if session_id:
            plat, chan, _ = parse_session_id(session_id)
            cap = get_memory_cap(plat, chan) if (plat and chan) else None
            if cap is not None:
                ok, beta = supports_cap(model, cap)
                if ok and beta:
                    betas.append(beta)
    except Exception:
        pass  # never fail the call over a beta-header optimization
    beta_header = ",".join(betas)

    if oauth_token:
        # Anthropic SDK auto-reads ANTHROPIC_API_KEY from env at client
        # construction and emits an X-Api-Key header EVEN when an explicit
        # auth_token= is also passed. Server then rejects with
        # "401 invalid x-api-key" because OAuth tokens aren't accepted on
        # that header. Pop the env var across construction so only the
        # Bearer header lands in auth_headers; restore immediately after
        # so other call sites that still rely on ANTHROPIC_API_KEY (e.g.,
        # provider routing, /status) keep working.
        _saved_env_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            client = anthropic.Anthropic(
                auth_token=oauth_token,
                default_headers={"anthropic-beta": beta_header},
            )
        finally:
            if _saved_env_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = _saved_env_key
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
