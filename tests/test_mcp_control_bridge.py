"""Fly's Class-A MCP control surface — the registry<->MCP bridge.

Proves against Fly's REAL capabilities (windyword.*, registered exactly as
the agent boots them) that:
  - tools/list is the band-filtered registry, reshaped to MCP, not re-derived
  - band filtering holds (a SANDBOX caller sees fewer tools than OWNER)
  - a capability's {ok:false} return surfaces as an MCP isError
No MCP SDK needed — the bridge is pure; server.py is the thin wrapper.
"""

from __future__ import annotations

import json

from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.windyword import register_windyword_capabilities
from windyfly.mcp_server.bridge import (
    band_denied_result,
    capability_result_to_mcp,
    registry_to_mcp_tools,
)


def _registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    register_windyword_capabilities(reg, None)  # 6 real USER-band capabilities
    return reg


def test_tools_list_is_the_registry_reshaped_to_mcp():
    reg = _registry()
    tools = registry_to_mcp_tools(reg, Band.OWNER)
    names = {t["name"] for t in tools}
    # the real windyword capabilities, verbatim from the registry
    assert "windyword.status" in names
    assert "windyword.set_master_volume" in names
    # MCP shape, not OpenAI function shape
    for t in tools:
        assert set(t) >= {"name", "description", "inputSchema"}
        assert "function" not in t
        assert t["inputSchema"]["type"] == "object"


def test_mcp_tools_match_the_agents_own_tool_list():
    # The projection must equal what the agent's LLM sees at the same band —
    # no drift, because we reshape rather than re-derive.
    reg = _registry()
    agent_names = [s["function"]["name"] for s in reg.tool_schemas_for_band(Band.OWNER)]
    mcp_names = [t["name"] for t in registry_to_mcp_tools(reg, Band.OWNER)]
    assert mcp_names == agent_names


def test_band_filtering_holds_over_mcp():
    reg = _registry()
    owner = {t["name"] for t in registry_to_mcp_tools(reg, Band.OWNER)}
    sandbox = {t["name"] for t in registry_to_mcp_tools(reg, Band.SANDBOX)}
    # windyword caps are USER-band → a SANDBOX caller sees none of them.
    assert owner, "owner should see the windyword capabilities"
    assert sandbox < owner, "sandbox must see a strict subset (the moat)"
    assert not (sandbox & owner) or sandbox != owner


def test_ok_false_becomes_mcp_iserror():
    block = capability_result_to_mcp({"ok": False, "error": "app not running"})
    assert block.get("isError") is True
    assert "app not running" in block["content"][0]["text"]


def test_ok_result_is_plain_content():
    block = capability_result_to_mcp({"ok": True, "volume": 40})
    assert "isError" not in block
    assert json.loads(block["content"][0]["text"])["volume"] == 40


def test_string_result_passes_through():
    block = capability_result_to_mcp("done")
    assert block["content"][0]["text"] == "done"
    assert "isError" not in block


def test_band_denied_result_is_structured():
    block = band_denied_result("windyword.set_setting", "requires TRUSTED; session is USER")
    assert block["isError"] is True
    payload = json.loads(block["content"][0]["text"])
    assert payload["ok"] is False and payload["error"] == "denied"
    assert payload["capability"] == "windyword.set_setting"


def test_build_registry_is_runnable_and_partial():
    # The opt-in entrypoint builds a real (if partial) surface without the
    # boot context — proving the seam is honest, not a phantom import.
    from windyfly.mcp_server.server import build_registry

    reg = build_registry()
    tools = registry_to_mcp_tools(reg, Band.OWNER)
    assert any(t["name"].startswith("windyword.") for t in tools)


def test_build_registry_full_exposes_the_whole_capability_plane(monkeypatch, tmp_path):
    # Default (non-minimal) build runs the real boot sequence → the WHOLE
    # Capability Plane is healable over MCP, not just the windyword subset.
    monkeypatch.delenv("WINDY_MCP_SERVER_MINIMAL", raising=False)
    # isolate: never touch the real agent db
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "mcp_test.db"))
    from windyfly.mcp_server.server import build_registry

    reg = build_registry()
    families = {t["name"].split(".")[0] for t in registry_to_mcp_tools(reg, Band.OWNER)}
    # core operational + doctor families must be present (more than windyword)
    assert {"fs", "shell", "health"} <= families, families
    assert len(families) > 1


def test_build_registry_minimal_flag_forces_subset(monkeypatch):
    monkeypatch.setenv("WINDY_MCP_SERVER_MINIMAL", "1")
    from windyfly.mcp_server.server import build_registry

    reg = build_registry()
    names = [t["name"] for t in registry_to_mcp_tools(reg, Band.OWNER)]
    assert names and all(n.startswith("windyword.") for n in names)
