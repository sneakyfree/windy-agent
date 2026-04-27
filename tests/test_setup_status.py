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


# ── setup.start walkthrough handler ────────────────────────────────


def test_setup_start_cloudflare_returns_walkthrough():
    from windyfly.agent.capabilities.setup import _start_handler
    out = _start_handler(integration="cloudflare")
    assert out["ok"] is True
    assert out["method"] == "token_paste"
    assert out["after_paste_action"] == "setup.save_credential"
    assert len(out["steps"]) >= 5
    # Critical: walkthrough must NOT echo the user's actual token —
    # the steps describe HOW to get one, not place a token in the text
    assert "cfat_" not in " ".join(out["steps"])  # only in note about format
    # The note tells the LLM what to do next
    assert "setup.save_credential" in out["note_to_llm"]
    assert "Do NOT echo" in out["note_to_llm"] or "Don't echo" in out["note_to_llm"]


def test_setup_start_github_returns_walkthrough():
    from windyfly.agent.capabilities.setup import _start_handler
    out = _start_handler(integration="github")
    assert out["ok"] is True
    assert out["method"] == "token_paste"


def test_setup_start_gmail_marks_oauth_required():
    from windyfly.agent.capabilities.setup import _start_handler
    out = _start_handler(integration="gmail")
    assert out["ok"] is True
    assert out["method"] == "oauth_required"
    assert out["after_paste_action"] is None


def test_setup_start_unknown_integration_returns_error():
    from windyfly.agent.capabilities.setup import _start_handler
    out = _start_handler(integration="not-a-thing")
    assert out["ok"] is False
    assert "No setup walkthrough" in out["error"]


# ── setup.save_credential — Cloudflare validate + persist + hot-load


def test_save_credential_cloudflare_happy_path(tmp_path, monkeypatch):
    """End-to-end: valid token → validates via API mock → writes env →
    hot-loads into os.environ → setup_status sees configured."""
    from unittest.mock import patch
    from windyfly.agent.capabilities import setup as setup_mod

    env_file = tmp_path / "windy.env"
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    fake_validator_result = (True, None, {"zones_visible": 21})
    fake_validators = {
        **setup_mod._SAVERS,
        "cloudflare": {
            **setup_mod._SAVERS["cloudflare"],
            "validator": lambda v: fake_validator_result,
        },
    }
    with patch.object(setup_mod, "_SAVERS", fake_validators):
        out = setup_mod._save_credential_handler(
            integration="cloudflare",
            value="cfat_TESTtokenABCDEFGHIJKLMNOPQRSTU",
            env_file=env_file,
        )

    assert out["ok"] is True
    assert out["env_var"] == "CLOUDFLARE_API_TOKEN"
    assert out["hot_loaded"] is True
    assert out["validation"]["zones_visible"] == 21
    # Persisted to env file
    contents = env_file.read_text()
    assert "CLOUDFLARE_API_TOKEN=cfat_TESTtokenABCDEFGHIJKLMNOPQRSTU" in contents
    # Hot-loaded into the running process
    assert os.environ["CLOUDFLARE_API_TOKEN"] == "cfat_TESTtokenABCDEFGHIJKLMNOPQRSTU"
    # cloudflare is now in configured_keys
    assert "cloudflare" in out["configured_keys"]


def test_save_credential_validation_failure_does_not_persist(tmp_path, monkeypatch):
    """If the API rejects the token, do NOT write it to env."""
    from unittest.mock import patch
    from windyfly.agent.capabilities import setup as setup_mod

    env_file = tmp_path / "windy.env"
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    fake_validators = {
        **setup_mod._SAVERS,
        "cloudflare": {
            **setup_mod._SAVERS["cloudflare"],
            "validator": lambda v: (False, "401 unauthorized", None),
        },
    }
    with patch.object(setup_mod, "_SAVERS", fake_validators):
        out = setup_mod._save_credential_handler(
            integration="cloudflare",
            value="cfat_INVALIDtokenABCDEFGHIJKLMNOP",
            env_file=env_file,
        )

    assert out["ok"] is False
    assert out["kind"] == "validation_failed"
    assert "401" in out["error"]
    # Critical: must NOT have persisted the bad token
    assert not env_file.exists() or "INVALID" not in env_file.read_text()
    assert "CLOUDFLARE_API_TOKEN" not in os.environ or \
           os.environ.get("CLOUDFLARE_API_TOKEN") != "cfat_INVALIDtokenABCDEFGHIJKLMNOP"


def test_save_credential_oauth_integration_returns_oauth_required():
    from windyfly.agent.capabilities.setup import _save_credential_handler
    out = _save_credential_handler(
        integration="gmail", value="some-pasted-thing-doesnt-matter",
    )
    assert out["ok"] is False
    assert out["kind"] == "oauth_required"
    assert "OAuth" in out["error"]


def test_save_credential_unknown_integration_returns_error():
    from windyfly.agent.capabilities.setup import _save_credential_handler
    out = _save_credential_handler(
        integration="not-a-thing", value="some-token",
    )
    assert out["ok"] is False
    assert "No save flow" in out["error"]


def test_save_credential_empty_value_refused(tmp_path):
    from windyfly.agent.capabilities.setup import _save_credential_handler
    out = _save_credential_handler(
        integration="cloudflare", value="",
        env_file=tmp_path / "x.env",
    )
    assert out["ok"] is False
    assert "empty" in out["error"]


def test_save_credential_malformed_token_refused_before_api_call(tmp_path):
    """Pattern check happens BEFORE the API call — saves a round trip."""
    from windyfly.agent.capabilities.setup import _save_credential_handler
    out = _save_credential_handler(
        integration="cloudflare",
        value="not a real token (has spaces)",
        env_file=tmp_path / "x.env",
    )
    assert out["ok"] is False
    assert "expected token shape" in out["error"]


# ── _atomic_upsert_env_var ─────────────────────────────────────────


def test_atomic_upsert_creates_new_file(tmp_path):
    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    env_file = tmp_path / "fresh.env"
    _atomic_upsert_env_var(env_file, "FOO", "bar")
    assert env_file.read_text() == "FOO=bar\n"


def test_atomic_upsert_appends_to_existing_file(tmp_path):
    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    env_file = tmp_path / "existing.env"
    env_file.write_text("EXISTING=keep_me\n")
    _atomic_upsert_env_var(env_file, "NEW", "added")
    contents = env_file.read_text()
    assert "EXISTING=keep_me" in contents
    assert "NEW=added" in contents


def test_atomic_upsert_replaces_existing_var(tmp_path):
    """Idempotent: re-saving a token replaces the old line, doesn't dup it."""
    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    env_file = tmp_path / "rotate.env"
    env_file.write_text("FOO=old_value\nOTHER=keep\n")
    _atomic_upsert_env_var(env_file, "FOO", "new_value")
    contents = env_file.read_text()
    assert "FOO=new_value" in contents
    assert "FOO=old_value" not in contents
    assert "OTHER=keep" in contents
    # Exactly one FOO= line
    assert contents.count("FOO=") == 1


def test_atomic_upsert_no_temp_file_left(tmp_path):
    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    env_file = tmp_path / "x.env"
    _atomic_upsert_env_var(env_file, "FOO", "bar")
    assert not list(tmp_path.glob("*.windy.tmp"))


def test_atomic_upsert_chmod_600(tmp_path):
    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    env_file = tmp_path / "secret.env"
    _atomic_upsert_env_var(env_file, "TOKEN", "value")
    mode = env_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_atomic_upsert_writes_through_symlink_does_not_replace_link(tmp_path):
    """Regression: industrial hardening sweep 2026-04-27 found that
    when the env file path was a symlink to a real file, naive
    ``os.replace`` was REPLACING the symlink with a regular file —
    silently breaking shared/fleet env layouts where the link points
    at a canonical store. Fix resolves the symlink first so the
    temp+rename happens at the real target."""
    import os as _os
    real = tmp_path / "real.env"
    real.write_text("EXISTING=keep_me\n")
    link = tmp_path / "link.env"
    _os.symlink(real, link)
    assert link.is_symlink()

    from windyfly.agent.capabilities.setup import _atomic_upsert_env_var
    _atomic_upsert_env_var(link, "NEW_TOKEN", "abc123")

    # The symlink must STILL be a symlink afterwards.
    assert link.is_symlink(), (
        "writing through a symlinked env file must not replace the link"
    )
    # The real file must contain BOTH the original line and the new one.
    real_text = real.read_text()
    assert "EXISTING=keep_me" in real_text
    assert "NEW_TOKEN=abc123" in real_text
    # Reading via the link must show the same content as the real file.
    assert link.read_text() == real_text


# ── Capability registration: 3 caps total now ─────────────────────


def test_setup_module_registers_three_capabilities():
    from windyfly.agent.capabilities.setup import register_setup_capabilities
    registry = CapabilityRegistry()
    register_setup_capabilities(registry, config={})
    for cap_id in ("setup.status", "setup.start", "setup.save_credential"):
        cap = registry.get(cap_id)
        assert cap is not None, f"{cap_id} not registered"
        assert cap.audit_required is True
    # save_credential must require a higher band than status/start
    from windyfly.agent.capabilities.descriptor import Band, Tier
    assert registry.get("setup.save_credential").tier == Tier.WRITE_DESTRUCTIVE
    assert registry.get("setup.save_credential").band_required == Band.TRUSTED
