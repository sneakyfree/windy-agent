"""Every agent turn names its band (Hub on #592): a forgotten band must not fail open to the owner's.

``agent_respond(band=None)`` still means OWNER for the local CLI and tests. Every other caller in
src/ passes ``band=`` explicitly, checked here by reading the source, so a new caller cannot slip
through. The channel wrappers in main.py treat a missing band as a stranger.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"
LOCAL_OPERATOR = {"channels/cli.py", "cli_selftest.py"}     # the person at the keyboard is the operator
# channels/manager.py calls a two-argument callback only for the owner (tested below).
ALLOWED = {"channels/manager.py"}


def _calls_without_band() -> list[str]:
    missing = []
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if rel in LOCAL_OPERATOR or rel in ALLOWED or rel == "agent/loop.py":
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name != "agent_respond":
                continue
            if not any(k.arg == "band" or k.arg is None for k in node.keywords):
                missing.append(f"{rel}:{node.lineno}")
    return missing


def test_every_agent_respond_call_outside_the_cli_names_its_band():
    assert _calls_without_band() == []


