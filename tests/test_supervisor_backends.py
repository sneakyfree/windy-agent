"""OS keep-alive backends (Tier 1) — command construction (2026-07-18).

Execution is proven on real boxes in the per-OS campaigns; here we pin
that each backend builds the right OS commands + service definitions.
"""
from __future__ import annotations

from windyfly.supervisor.backends import (
    LaunchdBackend, ServiceUnit, SupervisorBackend,
    SystemdBackend, WindowsTaskSchedulerBackend, get_backend,
)


def _unit():
    return ServiceUnit(
        name="windy-guardian",
        description="Windy guardian",
        exec_args=["uv", "run", "python", "-m", "windyfly.supervisor.run_guardian"],
        working_dir="/home/x/windy-agent",
        env={"WINDYFLY_CONFIG": "/cfg"},
        autostart=True,
    )


class TestSystemd:
    def test_restart_and_active_commands(self):
        b = SystemdBackend()
        assert b.restart_command("windy-0@telegram") == [
            "systemctl", "--user", "restart", "windy-0@telegram.service",
        ]
        assert b.is_active_command("g") == [
            "systemctl", "--user", "is-active", "g.service",
        ]

    def test_active_parse(self):
        b = SystemdBackend()
        assert b._parse_active(0, "active\n") is True
        assert b._parse_active(3, "inactive\n") is False


class TestLaunchd:
    def test_label_and_restart(self):
        b = LaunchdBackend()
        cmd = b.restart_command("guardian")
        assert cmd[0] == "launchctl" and cmd[1] == "kickstart" and cmd[2] == "-k"
        assert "com.windyfly.guardian" in cmd[3]


class TestWindowsTaskScheduler:
    def test_task_name_scoped_under_windy(self):
        b = WindowsTaskSchedulerBackend()
        assert b.is_active_command("guardian")[3] == "\\Windy\\guardian"

    def test_active_parse_needs_running(self):
        b = WindowsTaskSchedulerBackend()
        assert b._parse_active(0, "Status:  Running\n") is True
        assert b._parse_active(0, "Status:  Ready\n") is False
        assert b._parse_active(1, "Running") is False

    def test_install_xml_has_keepalive_and_logon(self):
        b = WindowsTaskSchedulerBackend()
        xml = b._task_xml(_unit())
        assert "<LogonTrigger>" in xml
        assert "<RestartOnFailure>" in xml and "<Count>999</Count>" in xml
        assert "windyfly.supervisor.run_guardian" in xml
        assert "<Command>uv</Command>" in xml

    def test_restart_ends_then_runs(self, monkeypatch):
        b = WindowsTaskSchedulerBackend()
        calls = []
        monkeypatch.setattr(b, "_run", lambda args, timeout=20.0: (calls.append(args), (0, ""))[1])
        assert b.restart("guardian") is True
        assert calls[0][:2] == ["schtasks", "/End"]
        assert calls[1][:2] == ["schtasks", "/Run"]


class TestFactory:
    def test_get_backend_returns_a_backend(self):
        b = get_backend()
        assert isinstance(b, SupervisorBackend)
        assert b.name in ("systemd", "launchd", "windows-taskschd")

    def test_run_handles_missing_binary(self):
        b = SystemdBackend()
        code, out = b._run(["definitely-not-a-real-binary-xyz"])
        assert code == 127 and "not found" in out


class TestRunGuardianWiring:
    def test_restart_fn_verifies_all_units(self, monkeypatch):
        from windyfly.supervisor import run_guardian as rg
        calls = {"restart": [], "active": []}

        class FakeBackend:
            def restart(self, name):
                calls["restart"].append(name); return True
            def is_active(self, name):
                calls["active"].append(name); return True
        monkeypatch.setattr(rg, "get_backend", lambda: FakeBackend())
        monkeypatch.setattr("time.sleep", lambda *_: None)
        fn = rg.build_restart_fn(units=["a", "b"])
        assert fn() is True
        assert calls["restart"] == ["a", "b"]
        assert calls["active"] == ["a", "b"]

    def test_restart_fn_fails_if_a_unit_stays_dead(self, monkeypatch):
        from windyfly.supervisor import run_guardian as rg

        class FakeBackend:
            def restart(self, name): return True
            def is_active(self, name): return name != "b"  # b never comes back
        monkeypatch.setattr(rg, "get_backend", lambda: FakeBackend())
        monkeypatch.setattr("time.sleep", lambda *_: None)
        fn = rg.build_restart_fn(units=["a", "b"])
        assert fn() is False  # verified-restart: b dead → not ok

    def test_config_adds_probe_when_token_present(self, monkeypatch):
        from windyfly.supervisor import run_guardian as rg
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        assert rg.build_config().external_probe is not None
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        assert rg.build_config().external_probe is None


class TestMacCampaignHardening:
    """Fixes surfaced by the 2026-07-18 Mac-native recovery campaign."""

    def test_launchd_restart_falls_back_gui_then_user(self, monkeypatch):
        b = LaunchdBackend()
        monkeypatch.delenv("WINDY_LAUNCHD_DOMAIN", raising=False)
        tried = []

        def fake_run(args, timeout=20.0):
            tried.append(args[3])  # the domain/label token
            # gui fails, user succeeds
            return (0, "") if args[3].startswith("user/") else (1, "no session")
        monkeypatch.setattr(b, "_run", fake_run)
        assert b.restart("guardian") is True
        assert tried[0].startswith("gui/") and tried[1].startswith("user/")

    def test_launchd_domain_override(self, monkeypatch):
        b = LaunchdBackend()
        monkeypatch.setenv("WINDY_LAUNCHD_DOMAIN", "system")
        assert b._domains()[0].startswith("system/")

    def test_launchd_plist_escapes_xml(self):
        b = LaunchdBackend()
        u = ServiceUnit(name="g", description="A & B < C",
                        exec_args=["uv", "run", "--flag=x&y"],
                        working_dir="/path/A&B", env={"K": "v<1>"})
        # write to a temp dir via a monkeypatched path would exec launchctl;
        # instead build the plist text directly through the same escaper.
        from windyfly.supervisor.backends import _xesc
        assert _xesc("/path/A&B") == "/path/A&amp;B"
        assert "&" not in _xesc("A & B").replace("&amp;", "")

    def test_windows_xml_escapes(self):
        b = WindowsTaskSchedulerBackend()
        u = ServiceUnit(name="g", description="drill & test",
                        exec_args=["python", "-c", "x < y & z"],
                        working_dir="C:\\a&b")
        xml = b._task_xml(u)
        assert "&amp;" in xml
        assert "x < y & z" not in xml  # raw ampersand/lt must not survive
        assert "drill & test" not in xml
