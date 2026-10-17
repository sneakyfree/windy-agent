"""Every agent turn names its band (Hub on #592): a forgotten band must not fail open to the owner's.

``agent_respond(band=None)`` still means OWNER for the local CLI and tests. Every other caller in
src/ passes ``band=`` explicitly, checked here by reading the source, so a new caller cannot slip
through. The channel wrappers in main.py treat a missing band as a stranger.
"""

from __future__ import annotations

import ast
import asyncio
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


def test_the_channel_wrapper_treats_a_missing_band_as_a_stranger(monkeypatch):
    """main.py's _respond (built for the ChannelManager) fails closed when no band is given."""
    import windyfly.main as m
    from windyfly.agent.capabilities import Band

    src = Path(m.__file__).read_text()
    assert src.count("band = Band.SANDBOX") == 2
    assert "band = Band.USER if is_guest_active() else Band.OWNER" not in src
    assert asyncio  # keeps the import used if this test grows a live run
    assert Band.SANDBOX < Band.USER


def test_a_callback_without_a_band_only_ever_hears_the_owner(monkeypatch):
    from windyfly.channels.base import IncomingMessage
    from windyfly.channels.manager import ChannelManager

    monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
    heard = []

    async def respond(text, session_id):
        heard.append(text)
        return "ok"

    mgr = ChannelManager(respond)

    def msg(sender):
        return IncomingMessage(platform="discord", channel_id="c1", sender_id=sender, sender_name="x", text="hi")

    assert asyncio.run(mgr._handle_message(msg("999"))) == ""         # a stranger: dropped
    assert asyncio.run(mgr._handle_message(msg("111"))) == "ok"        # the owner: heard
    assert heard == ["hi"]

