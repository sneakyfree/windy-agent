"""The owner-turn tool count, written as a fact at every start (journey probe check A, Boss 10-10).

Windy Mind refuses a request with more than 128 tools (422); on 10-10 an owner turn carried 129 and every
owner message fell to the local model, which no probe saw because the probe is not the owner. Now each start
writes ``<state dir>/owner-tools.json`` = {"owner_tools": n, "mind_max_tools": 128, "written_at": ...}, and the
journey job fails when ``owner_tools`` is over the cap. A fact, no model call, no owner power for the probe.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIND_MAX_TOOLS = 128
FILE_NAME = "owner-tools.json"


def owner_tool_count(tool_registry: Any, capability_registry: Any) -> int:
    """What an OWNER turn sends: the legacy tools plus the owner-band capabilities (as agent/loop.py builds it)."""
    from windyfly.agent.capabilities import Band

    legacy = tool_registry.get_schemas() if tool_registry else []
    caps = capability_registry.tool_schemas_for_band(Band.OWNER) if capability_registry else []
    return len(legacy) + len(caps)


def write(count: int, state_dir: Path | None = None) -> Path:
    """Atomic write of the fact file; returns its path."""
    from windyfly.platform import windy_state_dir

    target_dir = state_dir or windy_state_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / FILE_NAME
    body = {"owner_tools": count, "mind_max_tools": MIND_MAX_TOOLS,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    fd, tmp = tempfile.mkstemp(dir=str(target_dir), prefix=".owner-tools.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(tmp, path)
    return path


def record(tool_registry: Any, capability_registry: Any) -> int:
    """Boot step: count, log one line, write the fact. Never under pytest (no stray files in a real ~/.windy)."""
    count = owner_tool_count(tool_registry, capability_registry)
    level = logging.WARNING if count > MIND_MAX_TOOLS else logging.INFO
    logger.log(level, "[tools] an owner turn carries %d tools (Windy Mind's cap: %d)", count, MIND_MAX_TOOLS)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        write(count)
    return count
