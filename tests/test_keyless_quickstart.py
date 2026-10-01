"""Keyless config — the zero-key Windy Mind brain (Sprint 5).

``write_keyless_config`` writes a Windy Mind config (no API key); the
passport the brain uses comes from the hatch ceremony. ``windy bring-home``
reuses this writer. (The terminal keyless hatch flow was removed in 0.7.5 —
ADR-059, one hallway.)
"""

from __future__ import annotations

import pytest

from windyfly import quickstart as qs


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "PROJECT_ROOT", tmp_path)
    # setup_wizard PRESETS is read by the config writer.
    return tmp_path


class TestKeylessConfig:
    def test_writes_mind_config_no_api_key(self, project):
        qs.write_keyless_config()
        env = (project / ".env").read_text(encoding="utf-8")
        toml = (project / "windyfly.toml").read_text(encoding="utf-8")
        assert f"DEFAULT_MODEL={qs.KEYLESS_MODEL}" in env
        assert "MIND_API_URL=https://api.windymind.ai" in env
        assert "WINDY_MIND_SEND_TOOLS=1" in env
        assert "ETERNITAS_PASSPORT_TOKEN=" in env
        # No real provider key set anywhere.
        assert "sk-" not in env
        assert f'default_model = "{qs.KEYLESS_MODEL}"' in toml

    def test_tools_enabled_so_agent_can_act(self, project):
        # Without this flag the agent would skip Mind for every tool
        # turn and never be able to DO anything — pin it.
        qs.write_keyless_config()
        assert "WINDY_MIND_SEND_TOOLS=1" in (project / ".env").read_text(encoding="utf-8")

    def test_is_keyless_configured_detects_it(self, project):
        assert qs.is_keyless_configured() is False
        qs.write_keyless_config()
        assert qs.is_keyless_configured() is True

    def test_keyed_config_is_not_keyless(self, project):
        qs.write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        assert qs.is_keyless_configured() is False


class TestGeneratedEnvNamesTheIssuer:
    """The generated .env pins ETERNITAS_URL (clean-machine journey, 2026-09-23)."""

    def test_keyless_env_defaults_to_production_issuer(self, project, monkeypatch):
        monkeypatch.delenv("ETERNITAS_URL", raising=False)
        qs.write_keyless_config()
        env = (project / ".env").read_text()
        assert "ETERNITAS_URL=https://api.eternitas.ai" in env

    def test_keyed_env_defaults_to_production_issuer(self, project, monkeypatch):
        monkeypatch.delenv("ETERNITAS_URL", raising=False)
        qs.write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        assert "ETERNITAS_URL=https://api.eternitas.ai" in (project / ".env").read_text()

    def test_explicit_choice_is_kept(self, project, monkeypatch):
        monkeypatch.setenv("ETERNITAS_URL", "off")
        qs.write_keyless_config()
        assert "ETERNITAS_URL=off" in (project / ".env").read_text()
