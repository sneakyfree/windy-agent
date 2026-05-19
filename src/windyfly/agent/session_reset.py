"""Per-channel session reset + per-channel settings store.

Pre-PR-193 the bot built ``session_id = "{platform}:{channel_id}"``
once and used it forever. PR #193 introduced a rolling counter so
``/new`` increments it (``"telegram:8545546994:v3"``). PR #197
extends the same per-channel state to carry user preferences:

  - ``model``       — which model powers replies for this channel
                      (``/model opus`` etc., PR #197)
  - ``memory_cap``  — preferred context-window cap in tokens
                      (``/memory 1M`` etc., PR #197)

On-disk schema per channel:

    {
      "reset_count": int,           # /new counter
      "model":       str | None,    # /model preference
      "memory_cap":  int | None,    # /memory preference
    }

Persisted to ``~/.windy/session-counters.json`` by default,
overridable via ``WINDYFLY_SESSION_COUNTER_PATH`` env var (tests).
Atomic write (tempfile + rename). Thread-safe via a single module
lock. Backward compat: old plain-integer entries from PR-193-era
files auto-migrate to ``{"reset_count": N}`` on first load.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_counter_path() -> Path:
    """Resolve the state-file path. Order:
      1. WINDYFLY_SESSION_COUNTER_PATH env var (used by tests)
      2. ~/.windy/session-counters.json
    """
    override = os.environ.get("WINDYFLY_SESSION_COUNTER_PATH", "")
    if override:
        return Path(override)
    return Path.home() / ".windy" / "session-counters.json"


# Single in-process state. Lock guards both the dict and the file
# I/O so two concurrent writers (e.g., /new + /model from racing
# channels) can't corrupt the file or lose updates.
_lock = threading.Lock()
_state: dict[str, dict[str, Any]] | None = None
_persist_warned = False

_EMPTY_ENTRY: dict[str, Any] = {
    "reset_count": 0,
    "model": None,
    "memory_cap": None,
}


def _key(platform: str, channel_id: str) -> str:
    return f"{platform}:{channel_id}"


def _empty_entry() -> dict[str, Any]:
    return dict(_EMPTY_ENTRY)


def _load() -> dict[str, dict[str, Any]]:
    """Load full state from disk on first access. Returns empty dict
    if file missing / unreadable. Accepts both the new dict-shape
    schema and the legacy plain-integer schema from PR-193 era —
    legacy entries auto-upgrade to ``{"reset_count": N}`` so callers
    see a uniform shape.
    """
    global _state
    if _state is not None:
        return _state
    path = _default_counter_path()
    if not path.exists():
        _state = {}
        return _state
    try:
        raw = path.read_text()
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            logger.warning(
                "session-counters.json malformed (not a dict) — "
                "starting empty: %s", path,
            )
            _state = {}
            return _state
        out: dict[str, dict[str, Any]] = {}
        for k, v in parsed.items():
            if isinstance(v, dict):
                out[str(k)] = {
                    "reset_count": int(v.get("reset_count", 0)),
                    "model": v.get("model"),
                    "memory_cap": (
                        int(v["memory_cap"])
                        if v.get("memory_cap") is not None else None
                    ),
                }
            elif isinstance(v, int) or (
                isinstance(v, str) and v.lstrip("-").isdigit()
            ):
                # Legacy plain-integer counter — upgrade in place.
                out[str(k)] = {
                    "reset_count": int(v),
                    "model": None,
                    "memory_cap": None,
                }
        _state = out
        return _state
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "session-counters.json unreadable (%s) — starting empty",
            exc,
        )
        _state = {}
        return _state


def _save(state: dict[str, dict[str, Any]]) -> None:
    """Atomic write — temp file + rename. Logs once and continues if
    the path is unwritable; the bot must not die because state can't
    be persisted."""
    global _persist_warned
    path = _default_counter_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, sort_keys=True, indent=2))
        os.replace(tmp, path)
    except OSError as exc:
        if not _persist_warned:
            logger.warning(
                "could not persist session-counters.json to %s (%s) — "
                "settings will work for this process but won't survive "
                "restart. This warning won't repeat.", path, exc,
            )
            _persist_warned = True


# ── Public API ─────────────────────────────────────────────────────


def get_settings(platform: str, channel_id: str) -> dict[str, Any]:
    """Return this channel's full settings dict (a copy — safe to
    mutate). Missing channels return the default empty entry."""
    with _lock:
        state = _load()
        return dict(state.get(_key(platform, channel_id), _empty_entry()))


def get_reset_count(platform: str, channel_id: str) -> int:
    return int(get_settings(platform, channel_id).get("reset_count", 0))


def get_model(platform: str, channel_id: str) -> str | None:
    """Return the per-channel model preference, or None if the user
    hasn't picked one (caller should fall back to env / config
    default)."""
    return get_settings(platform, channel_id).get("model")


def get_memory_cap(platform: str, channel_id: str) -> int | None:
    """Return the per-channel memory cap preference (in tokens),
    or None if the user hasn't picked one (caller should fall back
    to the model's native cap from ``models_catalog``)."""
    return get_settings(platform, channel_id).get("memory_cap")


def set_model(platform: str, channel_id: str, model: str | None) -> None:
    """Set the per-channel model preference. ``None`` clears it
    (reverts to env / config default)."""
    _update_field(platform, channel_id, "model", model)


def set_memory_cap(
    platform: str, channel_id: str, cap: int | None,
) -> None:
    """Set the per-channel memory cap (in tokens). ``None`` clears
    it (reverts to the model's native cap)."""
    _update_field(platform, channel_id, "memory_cap", cap)


def _update_field(
    platform: str, channel_id: str, field: str, value: Any,
) -> None:
    if field not in ("model", "memory_cap"):
        raise ValueError(f"unknown setting: {field}")
    with _lock:
        state = _load()
        k = _key(platform, channel_id)
        entry = dict(state.get(k, _empty_entry()))
        entry[field] = value
        state[k] = entry
        _save(state)


def next_session_id(platform: str, channel_id: str) -> str:
    """Return the current rolling session_id for this channel.

    Shape: ``"{platform}:{channel_id}:v{N}"`` where N defaults to 0
    and increments on every ``reset_session()`` call.

    Idempotent and side-effect-free — safe to call on every incoming
    message in the channel handler.
    """
    n = get_reset_count(platform, channel_id)
    return f"{platform}:{channel_id}:v{n}"


def reset_session(platform: str, channel_id: str) -> str:
    """Increment the reset counter for this channel, clear the OLD
    session_id's token-tracker entry, persist, and return the NEW
    session_id. Preserves model + memory_cap preferences (they're
    settings, not session-scoped).
    """
    with _lock:
        state = _load()
        k = _key(platform, channel_id)
        entry = dict(state.get(k, _empty_entry()))
        old_n = int(entry.get("reset_count", 0))
        new_n = old_n + 1
        entry["reset_count"] = new_n
        state[k] = entry
        old_session_id = f"{platform}:{channel_id}:v{old_n}"
        new_session_id = f"{platform}:{channel_id}:v{new_n}"
        try:
            from windyfly.agent.loop import _session_tokens
            _session_tokens.pop(old_session_id, None)
        except ImportError:
            pass
        _save(state)
    logger.info(
        "session reset: %s -> %s (counter %d -> %d)",
        old_session_id, new_session_id, old_n, new_n,
    )
    return new_session_id


def parse_session_id(session_id: str) -> tuple[str, str, int]:
    """Split ``"telegram:8545546994:v3"`` into ``("telegram", "8545546994", 3)``.

    Accepts the legacy ``"{platform}:{channel_id}"`` shape too, returning
    version 0. Returns ``("", "", 0)`` for completely unparseable input —
    callers should treat that as "no per-channel state".
    """
    if not session_id or ":" not in session_id:
        return (session_id or "", "", 0)
    parts = session_id.rsplit(":", 2)
    if len(parts) == 3 and parts[2].startswith("v") and parts[2][1:].isdigit():
        return (parts[0], parts[1], int(parts[2][1:]))
    # Legacy "platform:channel_id" pre-PR-193
    platform, _, channel_id = session_id.partition(":")
    return (platform, channel_id, 0)


# Test-only: reset the module's in-memory state. Used by fixtures so
# one test's state doesn't leak into the next.
def _reset_module_state_for_tests() -> None:
    global _state, _persist_warned
    with _lock:
        _state = None
        _persist_warned = False
