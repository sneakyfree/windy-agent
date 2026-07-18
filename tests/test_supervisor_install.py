"""Supervisor install (last-mile wiring, 2026-07-18)."""
from __future__ import annotations

from pathlib import Path

from windyfly.supervisor.install import (
    build_units, install_supervisor, uninstall_supervisor,
)


class TestBuildUnits:
    def test_agent_per_channel_plus_guardian(self):
        units = build_units(project_root=Path("/x"),
                            channels=["telegram", "matrix"])
        names = [u.name for u in units]
        assert names == ["windy-agent-telegram", "windy-agent-matrix",
                         "windy-guardian"]

    def test_agent_units_run_the_channel(self):
        u = build_units(project_root=Path("/x"), channels=["telegram"])[0]
        assert "windyfly.main" in u.exec_args
        assert "--channel" in u.exec_args and "telegram" in u.exec_args
        assert u.autostart is True

    def test_guardian_knows_which_units_to_watch(self):
        units = build_units(project_root=Path("/x"),
                            channels=["telegram", "matrix"])
        g = units[-1]
        assert "run_guardian" in g.exec_args[-1]
        assert g.env["WINDY_GUARDIAN_UNITS"] == "windy-agent-telegram windy-agent-matrix"
        assert g.env["WINDY_GUARDIAN_CHANNELS"] == "telegram matrix"

    def test_config_path_threaded_into_env(self):
        units = build_units(project_root=Path("/x"), channels=["telegram"],
                            config_path="/cfg.toml")
        assert all(u.env.get("WINDYFLY_CONFIG") == "/cfg.toml" for u in units)


class _FakeBackend:
    name = "fake"
    def __init__(self): self.installed = []; self.uninstalled = []
    def install(self, unit): self.installed.append(unit.name); return True
    def uninstall(self, name): self.uninstalled.append(name); return True


class TestInstallUninstall:
    def test_install_all_units_via_backend(self):
        b = _FakeBackend()
        res = install_supervisor(project_root=Path("/x"),
                                 channels=["telegram"], backend=b)
        assert res == {"windy-agent-telegram": True, "windy-guardian": True}
        assert b.installed == ["windy-agent-telegram", "windy-guardian"]

    def test_partial_failure_reported(self):
        class HalfBackend(_FakeBackend):
            def install(self, unit):
                return unit.name != "windy-guardian"
        res = install_supervisor(project_root=Path("/x"),
                                 channels=["telegram"], backend=HalfBackend())
        assert res["windy-agent-telegram"] is True
        assert res["windy-guardian"] is False

    def test_uninstall_targets_agent_and_guardian(self):
        b = _FakeBackend()
        uninstall_supervisor(channels=["telegram"], backend=b)
        assert b.uninstalled == ["windy-agent-telegram", "windy-guardian"]
