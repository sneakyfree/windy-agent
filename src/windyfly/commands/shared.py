"""The shared owner command set (windy-contracts ``schema/chat/commands.v1.json``, owner: windy-chat).

One ecosystem list of slash commands, the same names/args/help on every engine (Chat's roster and
windyfly). The list is the contract's JSON ITSELF, vendored byte for byte in ``contracts/commands.v1.json``
(windy-contracts de745533, version 1.1.0) and pinned by a test, so there is no second hand-typed copy.
Chat owns the wording: a change is a windy-contracts PR, then a copy of the file here and the new hash in
``tests/test_shared_commands.py``. windyfly's other commands are not part of this set and stay as they are
(``/commands`` lists them all).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ENGINE = "windyfly"
CONTRACT = Path(__file__).parent / "contracts" / "commands.v1.json"


@lru_cache(maxsize=1)
def commands() -> tuple[dict[str, Any], ...]:
    """The contract's ``x-commands``: name, args, help, supported_by."""
    return tuple(json.loads(CONTRACT.read_text(encoding="utf-8"))["x-commands"])


def mine() -> list[tuple[str, str, str]]:
    """(name, args, help) of the shared commands this engine implements."""
    return [(c["name"], c.get("args", ""), c["help"]) for c in commands() if ENGINE in c["supported_by"]]


def help_text(exists=lambda name: True) -> str:
    """``/help`` for the owner: ``/<name> <args>: <help>`` per shared command this engine really has
    (``exists(name)``), then where the rest is."""
    lines = [f"/{n}{' ' + a if a else ''}: {h}" for n, a, h in mine() if exists(n)]
    lines.append("/commands: Every command I have.")
    return "\n".join(lines)
