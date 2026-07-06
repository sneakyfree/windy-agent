"""Keyless quickstart — the zero-key grandma path (Sprint 5).

`windy go` → "Free — no key needed" writes a Windy Mind config (no API
key), hatch mints + persists the Eternitas passport the brain uses, and
the agent launches. This is the ballroom flow.
"""

from __future__ import annotations

from unittest.mock import patch

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
        env = (project / ".env").read_text()
        toml = (project / "windyfly.toml").read_text()
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
        assert "WINDY_MIND_SEND_TOOLS=1" in (project / ".env").read_text()

    def test_is_keyless_configured_detects_it(self, project):
        assert qs.is_keyless_configured() is False
        qs.write_keyless_config()
        assert qs.is_keyless_configured() is True

    def test_keyed_config_is_not_keyless(self, project):
        qs.write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        assert qs.is_keyless_configured() is False


class TestKeylessFlow:
    def test_go_keyless_writes_config_hatches_launches(self, project):
        calls = []
        with patch.object(qs, "can_run", lambda _t: True), \
             patch.object(qs, "_try_hatch_provisioning",
                          side_effect=lambda *a, **k: calls.append("hatch")), \
             patch.object(qs, "_install_deps",
                          side_effect=lambda *a, **k: calls.append("deps")), \
             patch.object(qs, "_launch",
                          side_effect=lambda *a, **k: calls.append("launch")):
            qs._go_keyless(args=object())
        # Config written before hatch; hatch before launch.
        assert qs.is_keyless_configured()
        assert calls == ["hatch", "deps", "launch"]

    def test_go_keyless_installs_missing_prereqs(self, project):
        """The --keyless kiosk fast-path must install uv/bun itself — it skips
        the interactive Step 1 that would otherwise do it (finding B2)."""
        installed = []
        with patch.object(qs, "can_run", lambda _t: False), \
             patch.object(qs, "_install_prereqs",
                          side_effect=lambda m: installed.extend(m)), \
             patch.object(qs, "_try_hatch_provisioning"), \
             patch.object(qs, "_install_deps"), \
             patch.object(qs, "_launch"):
            qs._go_keyless(args=object())
        assert "uv" in installed and "bun" in installed

    def test_go_keyless_hatches_non_interactively_when_no_tty(self, project):
        """Piped/kiosk stdin (not a TTY) must run the hatch non-interactively,
        or the naming Prompt.ask hits EOF and the ceremony aborts (finding B1)."""
        captured = {}
        with patch.object(qs, "can_run", lambda _t: True), \
             patch.object(qs.sys.stdin, "isatty", lambda: False), \
             patch.object(qs, "_try_hatch_provisioning",
                          side_effect=lambda *a, **k: captured.update(k)), \
             patch.object(qs, "_install_deps"), \
             patch.object(qs, "_launch"):
            qs._go_keyless(args=object())
        assert captured.get("non_interactive") is True

    def test_keyless_brain_status_warns_on_mock_ept(self, project, capsys):
        (project / ".env").write_text("ETERNITAS_PASSPORT_TOKEN=mock-ept-abc\n")
        qs._report_keyless_brain_status()
        out = capsys.readouterr().out
        assert "couldn't connect" in out.lower()

    def test_keyless_brain_status_warns_on_empty_ept(self, project, capsys):
        (project / ".env").write_text("ETERNITAS_PASSPORT_TOKEN=\n")
        qs._report_keyless_brain_status()
        out = capsys.readouterr().out
        assert "couldn't connect" in out.lower()

    def test_keyless_brain_status_ok_on_real_ept(self, project, capsys):
        (project / ".env").write_text("ETERNITAS_PASSPORT_TOKEN=eyJhbGc.real.tok\n")
        qs._report_keyless_brain_status()
        out = capsys.readouterr().out
        assert "connected" in out.lower()

    def test_keyless_flag_short_circuits_interactive(self, project):
        class Args:
            key = None
            keyless = True

        with patch.object(qs, "_go_keyless") as mock_keyless:
            qs.cmd_go(Args())
        mock_keyless.assert_called_once()

    def test_menu_choice_1_is_keyless(self, project, monkeypatch):
        # Simulate the interactive menu picking option 1 (free path).
        class Args:
            key = None
            keyless = False

        # Clear any provider key the ambient env carries, or cmd_go's
        # "found a key in your environment" step short-circuits the menu.
        for pat in qs.KEY_PATTERNS:
            monkeypatch.delenv(pat["env_var"], raising=False)
        monkeypatch.setattr(qs, "can_run", lambda _tool: True)
        monkeypatch.setattr(qs, "_try_pro_broker", lambda _a: False)
        monkeypatch.setattr(qs, "read_clipboard", lambda: "")
        monkeypatch.setattr(qs.Confirm, "ask", staticmethod(lambda *a, **k: True))
        monkeypatch.setattr(qs.Prompt, "ask", staticmethod(lambda *a, **k: "1"))

        with patch.object(qs, "_go_keyless") as mock_keyless:
            qs.cmd_go(Args())
        mock_keyless.assert_called_once()
