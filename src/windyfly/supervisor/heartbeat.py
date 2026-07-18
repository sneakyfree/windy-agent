"""Cross-platform heartbeat file — the agent's 'I'm alive' signal.

The agent writes a tiny JSON heartbeat per channel every ~5 min; the
guardian reads it to tell a healthy agent from a wedged one WITHOUT
parsing logs (the fragile thing the old systemd probe did — it
reconstructed timestamps from log-line prefixes + file mtime). A
dedicated file is unambiguous and works identically on every OS.

Contract: {"ts": epoch_float, "pid": int, "channel": str,
"polling": bool}. Atomic write (tmp + os.replace) so the guardian
never reads a torn file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from windyfly.platform import windy_state_dir


def heartbeat_path(channel: str, state_dir: Path | None = None) -> Path:
    base = state_dir or windy_state_dir()
    return base / f"heartbeat-{channel}.json"


def write_heartbeat(
    channel: str,
    *,
    pid: int | None = None,
    polling: bool = True,
    state_dir: Path | None = None,
) -> None:
    """Best-effort atomic heartbeat write. Never raises into the caller."""
    try:
        path = heartbeat_path(channel, state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "pid": pid if pid is not None else os.getpid(),
            "channel": channel,
            "polling": bool(polling),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
    except Exception:
        pass  # a heartbeat write must never break the heartbeat loop


def read_heartbeat(
    channel: str, state_dir: Path | None = None,
) -> dict | None:
    # Retry-once (2026-07-18, Windows campaign): on Windows the writer's
    # os.replace can briefly collide with a read (sharing violation),
    # returning None for one tick. Harmless to the guardian (None →
    # conservative skip, never a false restart) but a retry seals the
    # torn-read window so a real signal isn't missed for a cycle. A
    # missing file (agent not started) short-circuits without retrying.
    path = heartbeat_path(channel, state_dir)
    if not path.exists():
        return None
    for attempt in range(2):
        try:
            return json.loads(path.read_text())
        except Exception:
            if attempt == 0:
                time.sleep(0.05)
                continue
            return None
    return None


def heartbeat_age(
    channel: str, state_dir: Path | None = None, *, now: float | None = None,
) -> float | None:
    """Seconds since the last heartbeat, or None if absent/unreadable."""
    hb = read_heartbeat(channel, state_dir)
    if not hb or "ts" not in hb:
        return None
    return (now if now is not None else time.time()) - float(hb["ts"])
