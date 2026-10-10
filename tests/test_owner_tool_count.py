"""An OWNER turn's tool list must fit Windy Mind's hard cap (windy-mind api/app/types.py ChatRequest.tools
max_length=128).

10-10: with agent teams on, my_mailbox made the 129th tool; Mind refused every owner turn (422) and the agent
fell to the local model. This runs the canonical boot registration (as main.py does) with the env a handed-home
agent carries plus teams on, in a SUBPROCESS (boot sets process-wide state), and counts an owner turn's tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

MIND_MAX_TOOLS = 128

_COUNT = r"""
import json, os, sys
from windyfly.agent.boot import BootContext, BootSequence, default_capability_registration_sequence
from windyfly.agent.capabilities import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.config import load_config
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
tools, caps = ToolRegistry(), CapabilityRegistry()
BootSequence(default_capability_registration_sequence()).run(BootContext(
    config=load_config(sys.argv[1]), db=Database(os.environ["WINDYFLY_DB_PATH"]), write_queue=WriteQueue(),
    tool_registry=tools, capability_registry=caps))
schemas = tools.get_schemas() + caps.tool_schemas_for_band(Band.OWNER)
print("NAMES=" + json.dumps([(s.get("function") or s).get("name") for s in schemas]))
"""


@pytest.fixture(scope="module")
def owner_tools(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("owner-tools")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")}
    env.update({
        "HOME": str(tmp), "WINDY_STATE_DIR": str(tmp), "WINDYFLY_DB_PATH": str(tmp / "t.db"),
        "WINDY_OWNER_BINDINGS_PATH": str(tmp / "owners.json"),
        "WINDY_TEAMS": "1", "WINDY_INVITE_GATE": "1", "WINDY_SEND_CONFIRM": "1",
        "MATRIX_HOMESERVER": "https://chat.example", "MATRIX_BOT_TOKEN": "t", "MATRIX_BOT_USER": "@a:chat.example",
        "MATRIX_DEVICE_ID": "D", "MATRIX_DM_ROOM_ID": "!dm:chat.example",
        "WINDYMAIL_EMAIL": "a@windymail.ai", "WINDYMAIL_JMAP_TOKEN": "t",
        "ETERNITAS_PASSPORT": "ET26-TEST-0001", "ETERNITAS_PASSPORT_TOKEN": "t",
        "MIND_API_URL": "https://mind.example", "WINDY_MIND_SEND_TOOLS": "1", "DEFAULT_MODEL": "windy-mind-auto",
        "WINDY_MIND_SELF": "1",  # on for every real agent
        "WINDY_HOUSING_REPORT": "0", "WINDY_DISABLE_AGENT_KEYS": "1",  # no network from a test
    })
    out = subprocess.run([sys.executable, "-c", _COUNT, os.path.join(repo, "windyfly.toml")], cwd=str(tmp), env=env,
                         capture_output=True, text=True, timeout=120)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("NAMES=")), "")
    assert line, out.stderr[-2000:]
    return json.loads(line[len("NAMES="):])


def test_an_owner_turn_fits_mind_s_tool_cap(owner_tools):
    assert len(owner_tools) <= MIND_MAX_TOOLS, f"{len(owner_tools)} tools > Mind's {MIND_MAX_TOOLS}"


def test_no_tool_is_offered_twice(owner_tools):
    assert len(owner_tools) == len(set(owner_tools))
