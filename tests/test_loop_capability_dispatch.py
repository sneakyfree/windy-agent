"""Tests for the agent loop's capability/tool dispatch routing.

Covers ``_dispatch_tool_call`` — the function in ``agent.loop`` that
picks between CapabilityRegistry and the legacy ToolRegistry when the
LLM emits a tool call. Capability wins on name collision; missing
capabilities fall through to legacy; unknown names get a typed JSON
error back to the LLM (so it can self-correct).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityDenied,
    CapabilityRegistry,
    Tier,
)
from windyfly.agent.loop import _dispatch_tool_call


def _cap_registry_with(*caps):
    r = CapabilityRegistry()
    for c in caps:
        r.register(c)
    return r


def test_capability_wins_when_name_matches():
    cap_reg = _cap_registry_with(Capability(
        id="ping",
        description="d",
        handler=lambda: "from-capability",
        tier=Tier.PURE_COMPUTE,
    ))
    legacy = MagicMock()
    legacy.execute.return_value = "from-legacy"

    result = _dispatch_tool_call(
        "ping", "{}", legacy, cap_reg, Band.OWNER, CapabilityDenied,
    )
    assert result == "from-capability"
    legacy.execute.assert_not_called()


def test_falls_through_to_legacy_when_no_capability():
    cap_reg = CapabilityRegistry()
    legacy = MagicMock()
    legacy.execute.return_value = "from-legacy"

    result = _dispatch_tool_call(
        "weather_get", '{"city": "SF"}', legacy, cap_reg,
        Band.OWNER, CapabilityDenied,
    )
    assert result == "from-legacy"
    legacy.execute.assert_called_once_with("weather_get", '{"city": "SF"}')


def test_unknown_tool_with_no_legacy_returns_json_error():
    cap_reg = CapabilityRegistry()
    result = _dispatch_tool_call(
        "nope", "{}", None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "nope" in parsed["error"]


def test_unknown_tool_with_legacy_keyerror_returns_json_error():
    cap_reg = CapabilityRegistry()
    legacy = MagicMock()
    legacy.execute.side_effect = KeyError("not found")
    result = _dispatch_tool_call(
        "nope", "{}", legacy, cap_reg, Band.OWNER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert "Unknown tool" in parsed["error"]


def test_capability_denied_returns_json_error_not_raise():
    cap_reg = _cap_registry_with(Capability(
        id="priv",
        description="d",
        handler=lambda: "ok",
        tier=Tier.FULL_MACHINE,  # TRUSTED+
    ))

    result = _dispatch_tool_call(
        "priv", "{}", None, cap_reg, Band.USER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert "capability_denied" in parsed["error"]


def test_capability_handler_exception_returns_json_error():
    def bad():
        raise ValueError("kaboom")

    cap_reg = _cap_registry_with(Capability(
        id="bad",
        description="d",
        handler=bad,
        tier=Tier.PURE_COMPUTE,
    ))
    result = _dispatch_tool_call(
        "bad", "{}", None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert "ValueError" in parsed["error"]
    assert "kaboom" in parsed["error"]


def test_capability_args_parsed_from_json_string():
    seen = []

    def h(**kwargs):
        seen.append(kwargs)
        return "ok"

    cap_reg = _cap_registry_with(Capability(
        id="echo",
        description="d",
        handler=h,
        tier=Tier.PURE_COMPUTE,
    ))
    result = _dispatch_tool_call(
        "echo", '{"a": 1, "b": "two"}',
        None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    assert result == "ok"
    assert seen == [{"a": 1, "b": "two"}]


def test_capability_args_invalid_json_returns_error():
    cap_reg = _cap_registry_with(Capability(
        id="echo",
        description="d",
        handler=lambda: "ok",
        tier=Tier.PURE_COMPUTE,
    ))
    result = _dispatch_tool_call(
        "echo", "not-json{{{", None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert "invalid JSON" in parsed["error"]


def test_capability_dict_result_serialized_to_json():
    cap_reg = _cap_registry_with(Capability(
        id="report",
        description="d",
        handler=lambda: {"status": "ok", "count": 3},
        tier=Tier.PURE_COMPUTE,
    ))
    result = _dispatch_tool_call(
        "report", "{}", None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    parsed = json.loads(result)
    assert parsed == {"status": "ok", "count": 3}


def test_capability_args_dict_passed_through_directly():
    """If the args come in as a dict (not a JSON string), use it as-is."""
    seen = []

    def h(**kwargs):
        seen.append(kwargs)
        return "ok"

    cap_reg = _cap_registry_with(Capability(
        id="echo",
        description="d",
        handler=h,
        tier=Tier.PURE_COMPUTE,
    ))
    result = _dispatch_tool_call(
        "echo", {"x": 1}, None, cap_reg, Band.OWNER, CapabilityDenied,
    )
    assert result == "ok"
    assert seen == [{"x": 1}]
