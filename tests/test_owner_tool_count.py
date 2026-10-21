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
# Boss (10-10, after the tool trim): a budget, so growth is a decision, not an accident. Raising it needs a
# reason in the PR. Integrations add their tools only when configured.
# Tool trim phase 1 (10-21): Google Calendar only when connected, the two builder/IDE status probes gone,
# builder start / vision / mcp / mind / cloudflare / setup merges. Budget = the measured count (was 88).
TOOL_BUDGET = 66
# Every optional family configured (GitHub, Cloudflare, SSH, Windy Word, health, the IDE, Calendar, the Code
# cabinet): 112 on 10-10. Phase 1: the measured count (was 118), at least 10 under Mind's hard cap.
FULL_BUDGET = 89

_COUNT = r"""
import json, os, sys
from windyfly.agent.boot import BootContext, BootSequence, default_capability_registration_sequence
from windyfly.agent.capabilities import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.config import load_config
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
# The count must not depend on the host: Windy Code's IDE leaves /tmp/windycode-agent.sock on a machine that
# runs it (Veron since 10-21 16:07), and windycode.configured() also counts a socket that merely exists. Unless
# this run configures the IDE on purpose (WINDYCODE_AGENT_SOCK), the default socket never counts here.
if not os.environ.get("WINDYCODE_AGENT_SOCK"):
    import windyfly.tools.windycode as _wc
    _wc._socket_path = lambda: "/nonexistent/windycode-agent.sock"
tools, caps = ToolRegistry(), CapabilityRegistry()
BootSequence(default_capability_registration_sequence()).run(BootContext(
    config=load_config(sys.argv[1]), db=Database(os.environ["WINDYFLY_DB_PATH"]), write_queue=WriteQueue(),
    tool_registry=tools, capability_registry=caps))
schemas = tools.get_schemas() + caps.tool_schemas_for_band(Band.OWNER)
print("NAMES=" + json.dumps([(s.get("function") or s).get("name") for s in schemas]))
"""


_BASE_ENV = {
    "WINDY_TEAMS": "1", "WINDY_INVITE_GATE": "1", "WINDY_SEND_CONFIRM": "1",
    "MATRIX_HOMESERVER": "https://chat.example", "MATRIX_BOT_TOKEN": "t", "MATRIX_BOT_USER": "@a:chat.example",
    "MATRIX_DEVICE_ID": "D", "MATRIX_DM_ROOM_ID": "!dm:chat.example",
    "WINDYMAIL_EMAIL": "a@windymail.ai", "WINDYMAIL_JMAP_TOKEN": "t",
    "ETERNITAS_PASSPORT": "ET26-TEST-0001", "ETERNITAS_PASSPORT_TOKEN": "t",
    "MIND_API_URL": "https://mind.example", "WINDY_MIND_SEND_TOOLS": "1", "DEFAULT_MODEL": "windy-mind-auto",
    "WINDY_MIND_SELF": "1",  # on for every real agent
    "WINDY_HOUSING_REPORT": "0", "WINDY_DISABLE_AGENT_KEYS": "1",  # no network from a test
}

# Every optional family switched on (the owner configured everything), e.g. Zero = 96 on 10-10.
_FULLY_CONFIGURED = {
    "GITHUB_PAT": "t", "CLOUDFLARE_API_TOKEN": "t", "WINDY_SSH_ALLOWED_HOSTS": "kit-test",
    "WINDY_WORD_CONTROL_TOKEN": "t", "WINDYCODE_AGENT_SOCK": "/nonexistent/windycode.sock",
    "WINDY_CALENDAR": "1", "WINDY_CODE_CABINET": "1",
}


def _owner_tool_names(tmp, extra: dict[str, str]) -> list[str]:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")}
    health = tmp / "health"
    health.mkdir(exist_ok=True)
    env.update(_BASE_ENV)
    env.update({"HOME": str(tmp), "WINDY_STATE_DIR": str(tmp), "WINDYFLY_DB_PATH": str(tmp / "t.db"),
                "WINDY_OWNER_BINDINGS_PATH": str(tmp / "owners.json"),
                "WINDY_HEALTH_DIR": str(health if extra else tmp / "no-health")})
    env.update(extra)
    out = subprocess.run([sys.executable, "-c", _COUNT, os.path.join(repo, "windyfly.toml")], cwd=str(tmp), env=env,
                         capture_output=True, text=True, timeout=120)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("NAMES=")), "")
    assert line, out.stderr[-2000:]
    return json.loads(line[len("NAMES="):])


@pytest.fixture(scope="module")
def owner_tools(tmp_path_factory):
    return _owner_tool_names(tmp_path_factory.mktemp("owner-tools"), {})


@pytest.fixture(scope="module")
def fully_configured_tools(tmp_path_factory):
    return _owner_tool_names(tmp_path_factory.mktemp("owner-tools-full"), _FULLY_CONFIGURED)


def test_an_owner_turn_fits_mind_s_tool_cap(owner_tools):
    assert len(owner_tools) <= MIND_MAX_TOOLS, f"{len(owner_tools)} tools > Mind's {MIND_MAX_TOOLS}"


def test_an_owner_turn_stays_within_the_tool_budget(owner_tools):
    assert len(owner_tools) <= TOOL_BUDGET, f"{len(owner_tools)} tools > budget {TOOL_BUDGET}: trim, or raise it on purpose"


def test_a_fully_configured_agent_stays_well_under_mind_s_cap(fully_configured_tools):
    """Boss (10-10): every optional family on can never reach the cap again."""
    n = len(fully_configured_tools)
    assert n <= FULL_BUDGET, f"fully configured: {n} tools > budget {FULL_BUDGET}: trim, or raise it on purpose"
    assert FULL_BUDGET <= MIND_MAX_TOOLS - 10  # room to spare under Mind's hard cap


def test_no_tool_is_offered_twice(owner_tools, fully_configured_tools):
    assert len(owner_tools) == len(set(owner_tools))
    assert len(fully_configured_tools) == len(set(fully_configured_tools))


def test_retired_names_are_never_offered(owner_tools, fully_configured_tools):
    """Tool trim phase 1: an alias is honoured at dispatch only, never offered in a schema."""
    from windyfly.tools.aliases import TOOL_ALIASES

    offered = set(owner_tools) | set(fully_configured_tools)
    assert not offered & set(TOOL_ALIASES)
    # every alias lands on a tool a fully configured owner turn really has
    assert {a.target for a in TOOL_ALIASES.values()} <= set(fully_configured_tools)
