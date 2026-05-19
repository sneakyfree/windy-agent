"""Per-channel session reset / rolling session_id support.

Pre-2026-05-19 the bot built ``session_id = "{platform}:{channel_id}"``
once and used it forever. Two consequences observed in the 2026-05-18
Telegram screenshot:

  1. ``_session_tokens[session_id]`` accumulated input+output tokens
     across every turn ever. After ~30 turns the cumulative was high
     enough that ``pct_remaining`` (computed against a 200K cap) fell
     below 10% and the ``LOW WORKING MEMORY`` block in prompt.py
     started firing on every reply — even on what the user considered
     a fresh start.

  2. ``/new`` returned the literal string ``"NEW_SESSION"`` which
     **nothing read**. The channel layer just posted the sentinel as
     a reply to the user. Neither the token counter nor
     ``get_recent_episodes()`` filtering changed, so the bot kept
     loading the same prior turns into its prompt and generating
     "in this long conversation" lines about a conversation the user
     thought they'd left behind.

This module introduces a per-(platform, channel_id) reset counter,
persisted to disk so it survives bot restart. ``session_id`` is now
``"{platform}:{channel_id}:v{N}"`` where N starts at 0 and increments
on every ``/new``. After a reset:

  - The OLD session_id's ``_session_tokens`` entry is cleared so it
    doesn't linger in the process dict forever.
  - The NEW session_id has no episodes tagged with it in the DB, so
    ``get_recent_episodes(session_id=...)`` returns empty until the
    user lands new turns. The model's prompt is genuinely fresh —
    NOT a relabel of stale context.

Counter persistence: ``~/.windy/session-counters.json`` by default,
overridable via ``WINDYFLY_SESSION_COUNTER_PATH`` env var (mostly for
tests). If the path is unwritable the module falls back to in-memory
only and logs a warning at first failure — bot still functions, just
loses /new survival across restarts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_counter_path() -> Path:
    """Resolve the counter-file path. Order:
      1. WINDYFLY_SESSION_COUNTER_PATH env var (used by tests)
      2. ~/.windy/session-counters.json
    """
    override = os.environ.get("WINDYFLY_SESSION_COUNTER_PATH", "")
    if override:
        return Path(override)
    return Path.home() / ".windy" / "session-counters.json"


# Single in-process state. Lock guards both the dict and the file
# I/O so two concurrent /new requests (e.g., from multiple channels)
# can't race and lose a bump.
_lock = threading.Lock()
_counters: dict[str, int] | None = None
_persist_warned = False


def _key(platform: str, channel_id: str) -> str:
    return f"{platform}:{channel_id}"


def _load_counters() -> dict[str, int]:
    """Load counters from disk on first access. Returns empty dict if
    file missing / unreadable — first /new will create it on save."""
    global _counters
    if _counters is not None:
        return _counters
    path = _default_counter_path()
    if not path.exists():
        _counters = {}
        return _counters
    try:
        raw = path.read_text()
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            logger.warning(
                "session-counters.json malformed (not a dict) — "
                "starting empty: %s", path,
            )
            _counters = {}
        else:
            # Defensive: cast values to int; ignore non-int.
            _counters = {
                str(k): int(v) for k, v in parsed.items()
                if isinstance(v, (int, str)) and str(v).lstrip("-").isdigit()
            }
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "session-counters.json unreadable (%s) — starting empty",
            exc,
        )
        _counters = {}
    return _counters


def _save_counters(counters: dict[str, int]) -> None:
    """Write counters atomically (write to .tmp then rename). Logs
    once and continues if the path is unwritable — the bot must not
    die because we can't persist a /new counter."""
    global _persist_warned
    path = _default_counter_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(counters, sort_keys=True, indent=2))
        os.replace(tmp, path)
    except OSError as exc:
        if not _persist_warned:
            logger.warning(
                "could not persist session-counters.json to %s (%s) — "
                "/new will work for this process but won't survive "
                "restart. This warning won't repeat.", path, exc,
            )
            _persist_warned = True


def get_reset_count(platform: str, channel_id: str) -> int:
    """Return the current /new reset count for this channel.

    Used by /status (PR #194) so the operator can see how many fresh
    starts they've done without parsing the v-suffix off
    ``next_session_id``. Returns 0 for channels that have never been
    reset (= they're on the original v0 session).
    """
    with _lock:
        counters = _load_counters()
        return counters.get(_key(platform, channel_id), 0)


def next_session_id(platform: str, channel_id: str) -> str:
    """Return the current rolling session_id for this channel.

    Shape: ``"{platform}:{channel_id}:v{N}"`` where N defaults to 0
    and increments on every successful ``reset_session()`` call.

    Idempotent and side-effect-free — safe to call on every incoming
    message in the channel handler.
    """
    with _lock:
        counters = _load_counters()
        n = counters.get(_key(platform, channel_id), 0)
    return f"{platform}:{channel_id}:v{n}"


def reset_session(platform: str, channel_id: str) -> str:
    """Increment the counter for this channel, clear the OLD
    session_id's token-tracker entry, persist, and return the NEW
    session_id.

    Returns the new ``"{platform}:{channel_id}:v{N+1}"`` string so the
    caller (cmd_new) can include it in confirmation telemetry if
    desired.
    """
    with _lock:
        counters = _load_counters()
        k = _key(platform, channel_id)
        old_n = counters.get(k, 0)
        new_n = old_n + 1
        counters[k] = new_n
        old_session_id = f"{platform}:{channel_id}:v{old_n}"
        new_session_id = f"{platform}:{channel_id}:v{new_n}"
        # Local import: avoid circular if loop.py ever needs to
        # import from this module (it doesn't today; defensive).
        try:
            from windyfly.agent.loop import _session_tokens
            _session_tokens.pop(old_session_id, None)
        except ImportError:
            pass
        _save_counters(counters)
    logger.info(
        "session reset: %s -> %s (counter %d -> %d)",
        old_session_id, new_session_id, old_n, new_n,
    )
    return new_session_id


# Test-only: reset the module's in-memory state. Used by fixtures so
# one test's counter doesn't leak into the next.
def _reset_module_state_for_tests() -> None:
    global _counters, _persist_warned
    with _lock:
        _counters = None
        _persist_warned = False
