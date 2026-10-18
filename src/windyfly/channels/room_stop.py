"""The owner's stop, and which agent messages are turns at all (agent-teamwork.v1; Grant's order 10-17).

On 10-17 two of the owner's agents looped in a group room and the owner's "stop" did not stop them. In
windyfly the turn budget (#528) even treated any human message as a reset, so a "stop" bought the agents
six more replies each. Now:

- **Stop (P5).** In a room, the owner's stop word ("stop", "stop it", "please stop", "enough", "be
  quiet", ...) or an ``ai.windy.stop`` event from the owner silences this agent's replies to OTHER AGENTS
  in that room. A stop is answered with nothing (no model call, no reply, no push), and it never resets
  the turn budget. The owner's next normal message in that room resumes. Stops are kept in a small
  state file (room ids only), so a restart in the middle of a loop does not let the agents go on.
- **Addressed only (P1).** Another agent's message is a turn only when it is addressed to this agent: its
  ``m.mentions.user_ids`` names this agent, or its ``ai.windy.task.to`` does, or (teamwork off only) the
  room holds just the two of them. Everything else is not a turn and makes no model call.
"""

from __future__ import annotations

import re
import threading

STOP_EVENT = "ai.windy.stop"
TASK_FIELD = "ai.windy.task"

# The ONE canonical list in agent-teamwork.v1 (windy-contracts #158, x-runtime.one_to_one_stop_phrases; the
# roster matches it): case-insensitive, whitespace and trailing punctuation allowed, plus /stop.
_WORDS = frozenset({
    "stop", "stop it", "stop now", "stop talking", "please stop", "enough", "that's enough", "thats enough",
    "be quiet", "quiet", "silence", "shut up", "pause", "halt",
})
_stopped: set[str] = set()
_lock = threading.Lock()
_loaded = False
_seen: tuple[int, int] | None = None     # (mtime_ns, size) of the state file when last read


def _path():
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "room_stops.json"


def _stat() -> tuple[int, int] | None:
    try:
        st = _path().stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read() -> set[str]:
    """The stops on disk. A missing or corrupt file is no stops (and is logged), never an error."""
    import json
    import logging

    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).warning("room stops: state file unreadable (%s); treated as none",
                                            type(e).__name__)
        return set()
    return {r for r in data if isinstance(r, str)} if isinstance(data, list) else set()


def _load() -> None:
    """Stops survive a restart (Hub on #596), and a stop written by another process sharing this state
    directory is seen (Hub on #599): the file is the record; memory follows it when it changes."""
    global _loaded, _seen
    st = _stat()
    if _loaded and st == _seen:
        return
    _stopped.clear()
    _stopped.update(_read())
    _loaded, _seen = True, st


def _change(room: str, add: bool) -> None:
    """Read, change one room, write: under a file lock, so two processes never drop each other's stops.
    The new file is created 0600 (mkstemp) and moved into place; it is never world-readable."""
    import json
    import os
    import tempfile

    global _seen
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        (_stopped.add if add else _stopped.discard)(room)        # memory still stops this process
        return
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:                                       # no flock (Windows): best effort
            pass
        current = _read()
        if (room in current) == add:
            _stopped.clear()
            _stopped.update(current)
            _seen = _stat()
            return
        (current.add if add else current.discard)(room)
        tmp_fd, tmp = tempfile.mkstemp(prefix=".room_stops.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(sorted(current)))
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _stopped.clear()
        _stopped.update(current)
        _seen = _stat()
    except OSError:
        (_stopped.add if add else _stopped.discard)(room)
    finally:
        os.close(fd)


def is_stop_word(text: str | None) -> bool:
    """The whole message is a stop phrase, or its first word is /stop or !stop (case ignored; extra
    whitespace and trailing punctuation allowed). A sentence that mentions stopping is not ("stop the timer" stays a request).

    /stop is the contract's stop, not windyfly's old process-stop command: in a chat room it silences the
    agents and never shuts the agent down (that stays a terminal command)."""
    t = (text or "").replace("\u2019", "'").replace("\u2018", "'").strip().lower()     # iOS curly apostrophe
    t = re.sub(r"[\s.!?…]+$", "", t)
    t = " ".join(t.split())
    if t.split(" ", 1)[0] in ("/stop", "!stop"):     # "/stop now", "!stop": never the old process stop (Hub on #604)
        return True
    return t in _WORDS


def stop(room: str) -> None:
    with _lock:
        _load()
        _change(room, True)


def resume(room: str) -> None:
    with _lock:
        _load()
        if room in _stopped:
            _change(room, False)


def stopped(room: str) -> bool:
    with _lock:
        _load()
        return room in _stopped


def addressed_to(content: object, me: str | None, joined_members: int) -> bool:
    """Is another agent's message addressed to this agent (``me``)? By m.mentions or ai.windy.task.to. With
    teamwork off, a room of just the two of them also counts (today's pair rooms rely on it); with teamwork
    on, it does not, as agent-teamwork.v1 says (Hub on #158)."""
    from windyfly.channels import teamwork

    if joined_members == 2 and not teamwork.enabled():
        return True
    if not isinstance(content, dict) or not me:
        return False
    mentions = content.get("m.mentions")
    ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
    if isinstance(ids, list) and any(isinstance(u, str) and u == me for u in ids):
        return True
    task = content.get(TASK_FIELD)
    return isinstance(task, dict) and isinstance(task.get("to"), str) and task.get("to") == me


def is_owner(sender: str | None, config: dict | None = None) -> bool:
    """The room owner by id, not by band: guest mode lowers the owner's band, and a stop must still work."""
    from windyfly.channels.identity import owner_ids

    return bool(sender) and sender in (owner_ids(config).get("matrix") or set())


def reset_for_tests() -> None:
    global _loaded, _seen
    with _lock:
        _stopped.clear()
        _loaded, _seen = False, None      # tests run on their own WINDY_STATE_DIR
