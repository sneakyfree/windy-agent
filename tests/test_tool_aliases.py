"""One-release aliases for retired tool names (windyfly.tools.aliases).

A model that still calls an old name gets the tool that does the job now, with the alias's fixed arguments;
the old names are never offered in a schema. (The lean Windy Fly cut most aliased tools; the mechanism is
tested with the aliases that remain plus synthetic ones.)
"""

from __future__ import annotations

import json

import pytest

from windyfly.agent.capabilities import Band, Capability, CapabilityDenied, CapabilityRegistry, Tier
from windyfly.agent.loop import _dispatch_tool_call
from windyfly.tools import aliases
from windyfly.tools.bands import turn_band
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def demo_aliases(monkeypatch):
    table = dict(aliases.TOOL_ALIASES)
    table["demo_old"] = aliases.Alias("demo_new", rename={"slug": "template"})
    table["demo_blank"] = aliases.Alias("demo_new", fixed={"blank": True})
    table["cap.old"] = aliases.Alias("cap.new", fixed={"read_text": True})
    monkeypatch.setattr(aliases, "TOOL_ALIASES", table)


def test_resolve_renames_and_fixes_arguments(demo_aliases):
    assert aliases.resolve("demo_old", {"slug": "bakery", "name": "Rosa"}) == (
        "demo_new", {"template": "bakery", "name": "Rosa"})
    assert aliases.resolve("demo_blank", '{"name": "Garden Club"}') == (
        "demo_new", {"name": "Garden Club", "blank": True})
    # the alias's fixed argument wins over what the caller sent
    assert aliases.resolve("cap.old", {"url": "u", "read_text": False}) == ("cap.new", {"url": "u", "read_text": True})
    assert aliases.resolve("mind.list_models", None) == ("mind.status", {})


def test_resolve_leaves_everything_else_alone(demo_aliases):
    assert aliases.resolve("web_search", '{"q": "x"}') == ("web_search", '{"q": "x"}')
    # malformed JSON is passed through so the dispatcher reports it like for any tool
    assert aliases.resolve("demo_old", "{nope") == ("demo_new", "{nope")


def test_no_alias_points_at_another_alias_or_itself():
    for old, alias in aliases.TOOL_ALIASES.items():
        assert alias.target != old
        assert alias.target not in aliases.TOOL_ALIASES


def test_a_retired_capability_name_dispatches_to_the_merged_capability(demo_aliases):
    seen: list[dict] = []
    reg = CapabilityRegistry()
    reg.register(Capability(id="cap.new", description="d", tier=Tier.PURE_COMPUTE,
                            handler=lambda **kw: seen.append(kw) or {"ok": True}))
    out = _dispatch_tool_call("cap.old", '{"url": "https://x/sign.png"}', None, reg, Band.OWNER, CapabilityDenied)
    assert json.loads(out) == {"ok": True}
    assert seen == [{"url": "https://x/sign.png", "read_text": True}]


def test_a_retired_legacy_name_dispatches_to_the_merged_tool(demo_aliases):
    seen: list[dict] = []
    tools = ToolRegistry()
    tools.register(name="demo_new", description="d", parameters={"type": "object", "properties": {}},
                   fn=lambda **kw: seen.append(kw) or {"status": "ok"})
    with turn_band(Band.OWNER):
        out = _dispatch_tool_call("demo_old", '{"slug": "recipe-book"}', tools,
                                  CapabilityRegistry(), Band.OWNER, CapabilityDenied)
    assert json.loads(out)["status"] == "ok"
    assert seen == [{"template": "recipe-book"}]
    assert {s["function"]["name"] for s in tools.get_schemas()} == {"demo_new"}
