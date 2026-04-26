"""Anthropic tool-name sanitization regression tests.

Production log evidence (2026-04-22 windy-0):

    [windyfly.agent.models] WARNING: Provider anthropic (claude-sonnet-4-6)
    failed: Error code: 400 - {'type': 'error', 'error': {'type':
    'invalid_request_error', 'message': "tools.25.custom.name: String
    should match pattern '^[a-zA-Z0-9_-]{1,128}$'"}}

The original sanitizer only replaced dots, leaving any other illegal
character (space, slash, colon, unicode, …) to fail Anthropic's regex.
Dynamic tools — collaborators with user-supplied names — are the
primary source of those illegal chars.

These tests pin the contract: ``_sanitize_for_anthropic`` must produce
a string that matches Anthropic's regex for ANY input, and round-trip
losslessly for any name whose sanitized form fits in 128 chars.
"""

from __future__ import annotations

import re

import pytest

from windyfly.agent.models import (
    _ANTHROPIC_EMPTY_FALLBACK,
    _ANTHROPIC_MAX_NAME_LEN,
    _openai_tools_to_anthropic,
    _restore_from_anthropic,
    _sanitize_for_anthropic,
)

ANTHROPIC_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


# ── Output validity (the contract Anthropic enforces) ──────────────


@pytest.mark.parametrize(
    "name",
    [
        "simple_name",
        "fs.read_file",
        "agent.create_collaborator",
        "shell.exec",
        "tool with spaces",
        "path/to/tool",
        "scope:thing",
        "weird@name#chars!",
        "café_tool",          # unicode within BMP
        "fox_🦊_tool",        # codepoint outside BMP
        "name\twith\ttabs",
        "name\nwith\nnewlines",
        "name.with.many.dots.and spaces and / slashes",
        "a" * 200,            # length stress
        "x",                  # single char
    ],
)
def test_sanitized_name_matches_anthropic_regex(name: str) -> None:
    out = _sanitize_for_anthropic(name)
    assert ANTHROPIC_REGEX.match(out), (
        f"sanitized {name!r} → {out!r} fails Anthropic regex"
    )


def test_empty_name_falls_back_not_empty() -> None:
    assert _sanitize_for_anthropic("") == _ANTHROPIC_EMPTY_FALLBACK
    assert ANTHROPIC_REGEX.match(_sanitize_for_anthropic(""))


def test_truncation_keeps_under_128_chars() -> None:
    out = _sanitize_for_anthropic("a" * 500)
    assert len(out) <= _ANTHROPIC_MAX_NAME_LEN


def test_truncation_distinguishes_distinct_inputs() -> None:
    """Truncated names must include a hash suffix for uniqueness so two
    long names that share a prefix don't collide on the wire."""
    a = _sanitize_for_anthropic("a" * 200 + "X")
    b = _sanitize_for_anthropic("a" * 200 + "Y")
    assert a != b, "truncated names with different originals must differ"


# ── Round-trip (sanitize → restore == original) ────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "simple_name",
        "fs.read_file",
        "agent.create_collaborator",
        "shell.exec",
        "tool with spaces",
        "path/to/tool",
        "scope:thing",
        "café_tool",
        "weird@name#chars!",
        "name.with.many.dots and spaces and / slashes",
    ],
)
def test_round_trip_preserves_original(name: str) -> None:
    """Sanitize → restore must return the original (for non-truncated)."""
    sanitized = _sanitize_for_anthropic(name)
    restored = _restore_from_anthropic(sanitized)
    assert restored == name, (
        f"round-trip failed: {name!r} → {sanitized!r} → {restored!r}"
    )


def test_legacy_dot_marker_still_decodes() -> None:
    """Tools sanitized by the old (dot-only) sanitizer must continue to
    restore correctly — there may be persisted tool calls or in-flight
    state still using the legacy form."""
    legacy_sanitized = "fs__W_DOT__read_file"
    assert _restore_from_anthropic(legacy_sanitized) == "fs.read_file"


# ── End-to-end: the converter that actually feeds Anthropic ─────────


def test_openai_tools_with_illegal_chars_pass_anthropic_validation() -> None:
    """The bug in the wild: a collaborator named with a space made
    ``collaborator.Math Helper.send_message`` reach Anthropic, which
    400'd. Verify the full converter now produces a valid tool list."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "collaborator.Math Helper.send_message",
                "description": "send message to a long-running collaborator",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fs.read_file",
                "description": "read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    out = _openai_tools_to_anthropic(tools)
    for tool in out:
        assert ANTHROPIC_REGEX.match(tool["name"]), (
            f"{tool['name']!r} fails Anthropic regex"
        )
    # And the round-trip on a tool-call response must restore the original.
    assert _restore_from_anthropic(out[0]["name"]) == (
        "collaborator.Math Helper.send_message"
    )
    assert _restore_from_anthropic(out[1]["name"]) == "fs.read_file"


def test_openai_tools_missing_parameters_get_default_schema() -> None:
    """Defense-in-depth: sometimes a capability is registered without an
    explicit parameters schema. The converter must still produce a
    valid Anthropic input_schema."""
    tools = [{"type": "function", "function": {"name": "no_params"}}]
    out = _openai_tools_to_anthropic(tools)
    assert out[0]["input_schema"] == {"type": "object", "properties": {}}
