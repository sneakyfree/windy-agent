"""Offline mode — fallback when LLM APIs are unreachable.

Checks connectivity, tries local Ollama if available,
otherwise queues messages for later processing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from windyfly.platform import windy_state_dir
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def is_online() -> bool:
    """Check if LLM APIs are reachable. Lenient by default.

    Pre-fix this hit api.openai.com once with a 3-second timeout
    and went OFFLINE on any failure. A transient blip (DNS, CDN
    hop, momentary packet loss) made grandma see the offline-mode
    reply even when the bot was fine — surfaced 2026-04-29 by the
    v12 demo dry-run.

    New behavior:
      1. Probe the actual provider in use (Anthropic / OpenAI),
         not always OpenAI
      2. Retry once on timeout — small extra latency, much more
         reliable
      3. DEFAULT TO ONLINE if both probes fail. The downstream LLM
         call itself has cooldown-circuit-breaker logic; surface
         the real error rather than short-circuit to offline-mode
         on a flaky DNS query.
    """
    import httpx

    # Prefer the provider whose key is set; fall back to OpenAI.
    candidates: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        candidates.append("https://api.anthropic.com")
    if os.environ.get("OPENAI_API_KEY"):
        candidates.append("https://api.openai.com")
    if not candidates:
        # No keys at all — the LLM call will fail loudly with a
        # friendly message via the error classifier. Treat as online
        # so we don't double-fall-back to offline.
        return True

    for url in candidates:
        for attempt in (1, 2):
            try:
                resp = httpx.get(url, timeout=3)
                if 200 <= resp.status_code < 500:
                    return True
            except Exception as e:
                logger.debug(
                    "is_online probe %s attempt %d failed: %s", url, attempt, e,
                )
                continue

    # Both probes failed both attempts. Could be a real outage OR a
    # flaky local network. Default to ONLINE so the call attempt
    # surfaces a useful error if it really is broken — rather than
    # silently dropping the user into offline-mode.
    logger.warning(
        "is_online probes failed; defaulting to ONLINE so the call attempt "
        "produces a real error if the upstream is genuinely down",
    )
    return True


def is_ollama_available() -> bool:
    """Check if Ollama is running locally.

    Returns:
        True if Ollama is responding on localhost:11434.
    """
    import httpx
    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=2)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


def get_offline_response(
    user_message: str,
    context: list[dict[str, str]] | None = None,
) -> str:
    """Generate a response while offline.

    Tries local Ollama first, otherwise returns a queue notice.

    Args:
        user_message: The user's message.
        context: Optional conversation context.

    Returns:
        Response text (from Ollama or a queue notice). When the
        local model isn't available, we append the standard
        recovery hint so a confused grandma sees /reset and
        /resurrect right under the "I'm offline" copy. The Ollama
        success path doesn't get the hint — it's a real reply.
    """
    if is_ollama_available():
        return _call_ollama(user_message, context)

    from windyfly.observability.recovery_hint import with_recovery_hint
    return with_recovery_hint(
        "I'm currently offline and don't have a local model available. "
        "I'll process your message when connectivity returns. 🪰"
    )


# Default Ollama HTTP timeout. The previous 30s default was tuned
# for a model already warm in RAM serving a trivial prompt, which
# is not the typical lifeboat case. On a 2017-vintage iMac (i7-7700K,
# no GPU) llama3.2:3b takes ~14s end-to-end on "hi" cold, and
# prompt-eval on a typical conversation context (5 messages, ~3KB
# total) scales to 60-120s before the first token is generated.
# 30s was guaranteed to time out under the exact conditions
# lifeboat is supposed to rescue. 180s is generous enough that
# realistic CPU inference completes; the user can override
# downward on faster hardware via WINDY_OLLAMA_TIMEOUT_S.
_DEFAULT_OLLAMA_TIMEOUT_S = 180.0

# Per-message and message-count caps for the context we feed
# Ollama. Small local models don't benefit from long context the way
# frontier models do, and every extra token costs ~0.5-1s of
# prompt-eval on CPU. Hard truncation here is what keeps lifeboat
# usable on slow hardware. Aligns roughly with llama3.2:3b's sweet
# spot of <1K tokens for fast first-token latency.
_OFFLINE_CONTEXT_MAX_MESSAGES = 3
_OFFLINE_CONTEXT_MAX_CHARS_PER_MESSAGE = 400


def _pick_offline_model() -> str:
    """Pick the Ollama model for an offline / lifeboat reply.

    Priority (highest first):
      1. Resurrection-mode chosen model (the user's explicit pick).
      2. ``WINDY_OLLAMA_MODEL`` env override (operator-level).
      3. Auto-pick best from installed Ollama models — closes the
         bug where the hardcoded "llama3.2" default 404'd on
         machines that have e.g. "llama3.2:3b" installed but not
         the bare-name variant.
      4. Hardcoded "llama3.2" as last-resort (pre-PR backwards compat).
    """
    model = "llama3.2"
    try:
        from windyfly.agent.resurrect import (
            current_model as _r_model,
            list_installed_ollama_models,
            pick_best_model,
        )
        rmod = _r_model()
        if rmod:
            model = rmod
        else:
            installed = list_installed_ollama_models()
            best = pick_best_model(installed)
            if best:
                model = best
    except Exception:
        pass
    return os.environ.get("WINDY_OLLAMA_MODEL", model)


def _truncate_offline_context(
    context: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Aggressively trim conversation context for CPU-only inference.

    Each extra context message is ~0.5-1s of prompt eval on a 2017
    iMac. Lifeboat's job is "stay responsive on commodity hardware,"
    not "match frontier-context quality." So cap at 3 messages and
    400 chars each — enough for the model to know what topic the
    user is on, not enough to blow first-token latency past the
    timeout.
    """
    if not context:
        return []
    trimmed: list[dict[str, str]] = []
    for msg in context[-_OFFLINE_CONTEXT_MAX_MESSAGES:]:
        content = (msg.get("content") or "")[
            :_OFFLINE_CONTEXT_MAX_CHARS_PER_MESSAGE
        ]
        trimmed.append({"role": msg.get("role", "user"), "content": content})
    return trimmed


def _ollama_timeout_s() -> float:
    """Honor ``WINDY_OLLAMA_TIMEOUT_S`` override; otherwise default."""
    raw = os.environ.get("WINDY_OLLAMA_TIMEOUT_S")
    if not raw:
        return _DEFAULT_OLLAMA_TIMEOUT_S
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_OLLAMA_TIMEOUT_S


def warm_ollama_model(model: str | None = None) -> bool:
    """Pre-load a model into Ollama's RAM cache.

    Called right after ``resurrect()`` succeeds so the user's first
    lifeboat message hits a warm model instead of paying the ~1.5s
    model-load cost on top of CPU inference. Returns True on
    success, False otherwise — best-effort; failure is non-fatal
    because the user's actual chat will still try to load the
    model itself.
    """
    import httpx

    chosen = model or _pick_offline_model()
    try:
        # ``num_predict: 1`` keeps the warmup cheap: load weights,
        # generate one token, return. ~1.5s on the iMac vs ~14s for
        # a full reply, but the model stays resident for ~5 minutes
        # so the user's first chat is fast.
        resp = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": chosen,
                "prompt": "hi",
                "stream": False,
                "options": {"num_predict": 1},
            },
            timeout=_ollama_timeout_s(),
        )
        if resp.status_code != 200:
            return False
        logger.info("Ollama model %s warmed", chosen)
        return True
    except Exception as e:
        logger.debug("Ollama warmup failed (model=%s): %s", chosen, e)
        return False


def _call_ollama(
    user_message: str,
    context: list[dict[str, str]] | None = None,
) -> str:
    """Call local Ollama for offline response.

    See ``_pick_offline_model`` for model selection and
    ``_truncate_offline_context`` for the context-shrinking
    rationale (CPU inference costs ~1s/extra message).
    """
    import httpx

    model = _pick_offline_model()

    messages: list[dict[str, str]] = []
    messages.extend(_truncate_offline_context(context))
    messages.append({"role": "user", "content": user_message})

    timeout_s = _ollama_timeout_s()
    try:
        response = httpx.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
            timeout=timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not content.strip():
            raise RuntimeError("ollama returned empty content")
        _record_ollama_outcome(success=True)
        return content
    except Exception as e:
        _record_ollama_outcome(success=False)
        logger.error("Ollama call failed (model=%s): %s", model, e)
        # Distinguish a timeout from a transport / 5xx so the user
        # gets accurate guidance. A timeout on commodity hardware
        # usually means "the model IS thinking, just slowly" — a
        # connect failure means "Ollama itself isn't running."
        is_timeout = isinstance(e, httpx.TimeoutException) or "timed out" in str(e).lower()
        if is_timeout:
            bare = (
                f"My backup brain ({model}) is running, but took longer than "
                f"{int(timeout_s)}s to reply — that's CPU-only inference on a "
                "small local model. Your message is saved. Try a shorter "
                "question, or type /normal to switch back to the fast paid model."
            )
        else:
            bare = (
                f"Local model hiccup ({type(e).__name__}). Your message is "
                "queued; I'll try again when I'm healthier."
            )
        try:
            from windyfly.observability.recovery_hint import with_recovery_hint
            return with_recovery_hint(bare)
        except Exception:
            return bare


# ── Lifeboat stuck-state escape (consecutive-failure tracker) ───────
#
# Companion to ``resurrect.attempt_paid_recovery``: that one
# unsticks lifeboat when the PAID side comes back. This one unsticks
# lifeboat when the LOCAL side proves it can't deliver — three
# consecutive Ollama failures means the user is staring at error
# messages on every chat, and staying in lifeboat is worse than
# bouncing them back to the offline-queued message (at least the
# offline path tells them clearly what's happening).
_OLLAMA_FAILURE_COUNTER_PATH = Path(os.environ.get(
    "WINDY_OLLAMA_FAILURE_COUNTER",
    str(windy_state_dir() / ".ollama_fail_count"),
))
_OLLAMA_MAX_CONSECUTIVE_FAILURES = 3


def _record_ollama_outcome(success: bool) -> None:
    """Track consecutive Ollama failures so we can auto-escape from
    a wedged lifeboat. Resets on any success."""
    try:
        if success:
            _OLLAMA_FAILURE_COUNTER_PATH.unlink(missing_ok=True)
            return
        _OLLAMA_FAILURE_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        current = 0
        if _OLLAMA_FAILURE_COUNTER_PATH.exists():
            try:
                current = int(_OLLAMA_FAILURE_COUNTER_PATH.read_text().strip())
            except ValueError:
                current = 0
        _OLLAMA_FAILURE_COUNTER_PATH.write_text(str(current + 1))
    except Exception as e:
        logger.debug("ollama outcome record failed: %s", e)


def consecutive_ollama_failures() -> int:
    """Read-only count of consecutive Ollama failures since the last
    success. Used by the agent loop's lifeboat-escape check."""
    if not _OLLAMA_FAILURE_COUNTER_PATH.exists():
        return 0
    try:
        return int(_OLLAMA_FAILURE_COUNTER_PATH.read_text().strip())
    except (ValueError, OSError):
        return 0


def should_escape_lifeboat() -> bool:
    """True iff Ollama has failed N times in a row. Caller (loop)
    clears the resurrect flag and notifies the user."""
    return consecutive_ollama_failures() >= _OLLAMA_MAX_CONSECUTIVE_FAILURES


# ---------------------------------------------------------------------------
# Persistent offline message queue
# ---------------------------------------------------------------------------

_QUEUE_PATH = Path(os.environ.get(
    "WINDYFLY_OFFLINE_QUEUE",
    "data/offline_queue.json",
))


def queue_message(user_message: str, session_id: str = "") -> None:
    """Queue a message for processing when connectivity returns.

    Uses atomic write (temp file + rename) to prevent corruption.

    Args:
        user_message: The user's message.
        session_id: Session ID for continuity.
    """
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    queue = _load_queue()
    queue.append({
        "message": user_message,
        "session_id": session_id,
        "queued_at": __import__("datetime").datetime.now().isoformat(),
    })
    # Atomic write: write to temp file then rename
    tmp = _QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2))
    tmp.rename(_QUEUE_PATH)
    logger.info("Queued offline message (%d in queue)", len(queue))


def get_queued_messages() -> list[dict[str, str]]:
    """Return all queued messages."""
    return _load_queue()


def clear_queue() -> int:
    """Clear the queue and return how many were cleared."""
    count = len(_load_queue())
    if _QUEUE_PATH.exists():
        _QUEUE_PATH.unlink()
    return count


def replay_queued_messages(
    config: dict,
    db: "Database",
    write_queue: "WriteQueue",
    tool_registry: "ToolRegistry | None" = None,
) -> int:
    """Replay all queued messages now that we're back online.

    Args:
        config: Config dict.
        db: Database instance.
        write_queue: WriteQueue.
        tool_registry: Optional tool registry.

    Returns:
        Number of messages successfully replayed.
    """
    queue = get_queued_messages()
    if not queue:
        return 0

    from windyfly.agent.loop import agent_respond

    replayed = 0
    for msg in queue:
        try:
            agent_respond(
                config, db, write_queue,
                msg["message"], msg.get("session_id", "offline"),
                tool_registry,
            )
            replayed += 1
        except Exception as e:
            logger.error("Failed to replay queued message: %s", e)

    clear_queue()
    logger.info("Replayed %d/%d queued messages", replayed, len(queue))
    return replayed


def _load_queue() -> list[dict[str, str]]:
    """Load the queue from disk."""
    if _QUEUE_PATH.exists():
        try:
            return json.loads(_QUEUE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


