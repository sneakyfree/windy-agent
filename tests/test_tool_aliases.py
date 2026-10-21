"""One-release aliases for the names retired by tool trim phase 1 (windyfly.tools.aliases).

A model or a saved skill that still calls an old name gets the tool that does the job now, with the
alias's fixed arguments; the old names are never offered in a schema.
"""

from __future__ import annotations

import json

from windyfly.agent.capabilities import Band, Capability, CapabilityDenied, CapabilityRegistry, Tier
from windyfly.agent.loop import TAINT_FORBIDDEN_TOOLS, _dispatch_tool_call, _taint_gate
from windyfly.tools import aliases
from windyfly.tools.bands import turn_band
from windyfly.tools.registry import ToolRegistry


def test_resolve_renames_and_fixes_arguments():
    assert aliases.resolve("windycodeweb_start_from_template", {"slug": "bakery", "name": "Rosa"}) == (
        "windycodeweb_start", {"template": "bakery", "name": "Rosa"})
    assert aliases.resolve("windycodeweb_create_project", '{"name": "Garden Club"}') == (
        "windycodeweb_start", {"name": "Garden Club", "blank": True})
    # the alias's fixed argument wins over what the caller sent
    assert aliases.resolve("vision.ocr", {"url": "https://x/a.png", "read_text": False}) == (
        "vision.describe", {"url": "https://x/a.png", "read_text": True})
    assert aliases.resolve("mcp.list_servers", "") == ("mcp.list_tools", {})
    assert aliases.resolve("mind.list_models", None) == ("mind.status", {})


def test_resolve_leaves_everything_else_alone():
    assert aliases.resolve("web_search", '{"q": "x"}') == ("web_search", '{"q": "x"}')
    assert aliases.resolve("vision.describe", {"url": "u"}) == ("vision.describe", {"url": "u"})
    # malformed JSON is passed through so the dispatcher reports it like for any tool
    assert aliases.resolve("setup.start", "{nope") == ("setup.status", "{nope")


def test_no_alias_points_at_another_alias_or_itself():
    for old, alias in aliases.TOOL_ALIASES.items():
        assert alias.target != old
        assert alias.target not in aliases.TOOL_ALIASES


def test_a_retired_capability_name_dispatches_to_the_merged_capability():
    seen: list[dict] = []
    reg = CapabilityRegistry()
    reg.register(Capability(id="vision.describe", description="d", tier=Tier.PURE_COMPUTE,
                            handler=lambda **kw: seen.append(kw) or {"ok": True}))
    out = _dispatch_tool_call("vision.ocr", '{"url": "https://x/sign.png"}', None, reg,
                              Band.OWNER, CapabilityDenied)
    assert json.loads(out) == {"ok": True}
    assert seen == [{"url": "https://x/sign.png", "read_text": True}]


def test_a_retired_legacy_name_dispatches_to_the_merged_tool():
    seen: list[dict] = []
    tools = ToolRegistry()
    tools.register(name="windycodeweb_start", description="d", parameters={"type": "object", "properties": {}},
                   fn=lambda **kw: seen.append(kw) or {"status": "ok"})
    with turn_band(Band.OWNER):
        out = _dispatch_tool_call("windycodeweb_start_from_template", '{"slug": "recipe-book"}', tools,
                                  CapabilityRegistry(), Band.OWNER, CapabilityDenied)
        _dispatch_tool_call("windycodeweb_create_project", {"name": "Garden Club", "kind": "site"}, tools,
                            CapabilityRegistry(), Band.OWNER, CapabilityDenied)
    assert json.loads(out)["status"] == "ok"
    assert seen == [{"template": "recipe-book"}, {"name": "Garden Club", "kind": "site", "blank": True}]
    # the old names are not tools of their own
    assert {s["function"]["name"] for s in tools.get_schemas()} == {"windycodeweb_start"}


def test_retired_site_writers_are_still_refused_on_a_tainted_turn():
    for old in ("windycodeweb_create_project", "windycodeweb_start_from_template"):
        assert old not in TAINT_FORBIDDEN_TOOLS  # the list names the tool that does the job now
        deny, _ = _taint_gate(old, True, Band.OWNER)
        assert deny is True


def test_the_real_merged_tools_accept_the_alias_arguments():
    """Every alias's arguments are ones its target's real handler takes (no TypeError at dispatch)."""
    import inspect

    from windyfly.tools import windycode, windycode_web

    start = inspect.signature(windycode_web.windycodeweb_start).parameters
    assert {"template", "name", "fills", "blank", "kind"} <= set(start)
    assert not inspect.signature(windycode_web.windycodeweb_list_projects).parameters
    assert not inspect.signature(windycode.windycode_list_projects).parameters
