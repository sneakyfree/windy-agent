"""Tier 1 grandma-mode tests.

Validates:
  - get_setup_status() shape and dormant/configured key partitioning
  - is_configured(key) lookup
  - dormant_nudge(key) returns text that explicitly tells the LLM
    NOT to relay developer-only setup commands to the user
  - Each capability's dormant refusal now uses dormant_nudge() (no
    "windy setup-*" or "set CLOUDFLARE_API_TOKEN" parroted)
  - The new setup.status capability registers and returns the same
    shape as get_setup_status()
"""

from __future__ import annotations

import os

import pytest

from windyfly.agent.capabilities import CapabilityRegistry
from windyfly.agent.setup_status import (
    dormant_nudge, get_setup_status, is_configured,
)


# ── get_setup_status shape ─────────────────────────────────────────


def test_setup_status_returns_expected_top_level_shape(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = get_setup_status()
    assert set(out.keys()) == {
        "summary", "integrations", "configured_keys", "dormant_keys",
    }
    assert set(out["summary"].keys()) == {"configured", "dormant", "total"}
    assert out["summary"]["total"] == len(out["integrations"])
    assert (
        out["summary"]["configured"] + out["summary"]["dormant"]
        == out["summary"]["total"]
    )


def test_setup_status_includes_known_integrations(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    out = get_setup_status()
    keys = {i["key"] for i in out["integrations"]}
    assert {"gmail", "calendar", "cloudflare", "github"} <= keys


def test_setup_status_each_entry_has_required_fields(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    out = get_setup_status()
    for entry in out["integrations"]:
        assert "key" in entry
        assert "name" in entry
        assert "configured" in entry and isinstance(entry["configured"], bool)
        assert "setup_kinds" in entry
        assert "chat_intent" in entry
        # cli_command may legitimately be None (cloudflare/github)
        assert "cli_command" in entry


def test_dormant_keys_partition_correctly(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = get_setup_status()
    assert "cloudflare" in out["configured_keys"]
    assert "github" in out["dormant_keys"]
    assert "cloudflare" not in out["dormant_keys"]


# ── is_configured lookup ───────────────────────────────────────────


def test_is_configured_lookup_by_key(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake")
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert is_configured("cloudflare") is True
    assert is_configured("github") is False


def test_is_configured_unknown_key_returns_false():
    assert is_configured("nonexistent") is False


# ── dormant_nudge — the key contract for grandma-mode ──────────────


def test_dormant_nudge_includes_chat_intent():
    """Bot must offer the user a plain-English 'say this' next step."""
    text = dormant_nudge("cloudflare")
    assert "set up cloudflare" in text


def test_dormant_nudge_explicitly_warns_against_developer_jargon():
    """The KEY contract: nudge tells the LLM NOT to relay CLI commands.

    This is the line that prevents grandma from seeing
    'Run `windy setup-gmail`' in her Telegram chat. If this
    instruction is missing from the nudge, the LLM may helpfully
    parrot the technical command from the integration `note` field."""
    for key in ("gmail", "calendar", "cloudflare", "github"):
        text = dormant_nudge(key)
        assert "do NOT relay" in text or "do not relay" in text.lower(), (
            f"dormant_nudge({key!r}) missing the no-jargon instruction "
            f"to the LLM: {text!r}"
        )


def test_dormant_nudge_unknown_key_returns_generic_string():
    text = dormant_nudge("doesnotexist")
    assert "doesnotexist" in text
    assert "isn't recognized" in text


def test_dormant_nudge_for_cli_capable_integration_includes_optional_cli(
    monkeypatch,
):
    """If the integration has a CLI option, the nudge tells the LLM it's
    available BUT only for explicit power-users — not the default."""
    text = dormant_nudge("gmail")
    assert "windy setup-gmail" in text
    # And it must qualify the CLI as conditional
    assert "developer" in text.lower() or "operator" in text.lower()


# ── Each capability now uses dormant_nudge ─────────────────────────


def test_email_dormant_refusal_uses_grandma_friendly_text(tmp_path, monkeypatch):
    """Regression: the old text said 'Run `windy setup-gmail`' verbatim.
    The new text must NOT — it must come from dormant_nudge."""
    import windyfly.agent.capabilities.email as email_mod
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", tmp_path / "missing.json")

    out = email_mod._send_email_handler(
        to="a@b.com", subject="hi", body="x",
    )
    assert out["executed"] is False
    assert out["kind"] == "dormant_integration"
    assert out["integration"] == "gmail"
    # The error must include the LLM instruction (so it doesn't parrot
    # CLI commands), not be the old verbatim "Run `windy setup-gmail`".
    assert "do NOT relay" in out["error"]
    # Must offer the chat-driven path
    assert "set up email" in out["error"]


def test_cloudflare_dormant_refusal_uses_grandma_friendly_text():
    """Cloudflare's _not_configured_error() must come from dormant_nudge."""
    from windyfly.agent.capabilities.cloudflare import _list_zones_handler
    out = _list_zones_handler(token="")
    assert out["ok"] is False
    assert out["kind"] == "dormant_integration"
    assert out["integration"] == "cloudflare"
    assert "do NOT relay" in out["error"]
    assert "set up cloudflare" in out["error"]


def test_calendar_dormant_response_uses_grandma_friendly_text(monkeypatch):
    """Calendar's _not_configured_response() must come from dormant_nudge."""
    from windyfly.tools import calendar as calendar_module
    # Force is_configured() to return False
    monkeypatch.setattr(calendar_module, "_is_configured", lambda: False)
    out = calendar_module.get_today_events()
    assert out["kind"] == "dormant_integration"
    assert out["integration"] == "calendar"
    assert "do NOT relay" in out["message"]
    assert "set up calendar" in out["message"]


# ── setup.status capability ────────────────────────────────────────


def test_setup_status_capability_registers():
    from windyfly.agent.capabilities.setup import register_setup_capabilities
    registry = CapabilityRegistry()
    register_setup_capabilities(registry, config={})
    cap = registry.get("setup.status")
    assert cap is not None
    assert cap.audit_required is True


def test_setup_status_capability_returns_same_shape_as_helper(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake")
    from windyfly.agent.capabilities.setup import register_setup_capabilities
    registry = CapabilityRegistry()
    register_setup_capabilities(registry, config={})
    cap = registry.get("setup.status")
    out = cap.handler()
    assert "summary" in out
    assert "integrations" in out
    assert "cloudflare" in out["configured_keys"]


# ── Boot wiring ────────────────────────────────────────────────────


def test_boot_sequence_includes_capabilities_setup():
    from windyfly.agent.boot import default_capability_registration_sequence
    seq = default_capability_registration_sequence()
    names = [s.name for s in seq]
    assert "capabilities.setup" in names
    setup_idx = names.index("capabilities.setup")
    audit_idx = names.index("capabilities.audit")
    assert setup_idx > audit_idx
    setup_step = next(s for s in seq if s.name == "capabilities.setup")
    assert "capabilities.audit" in setup_step.requires
