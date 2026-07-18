"""OS keep-alive backends (Tier 1) — the ONLY OS-specific code.

A crashed process can't restart itself; only the OS can. Each backend
is a thin adapter over one OS's service manager, exposing the same
tiny interface:

    install(unit)   — register a process to auto-start + be kept alive
    uninstall(name) — remove it
    is_active(name) — is it running?
    restart(name)   — restart it (what the guardian calls)

'Unify on the guardian model' (Grant 2026-07-18): the OS layer only
keeps processes alive (the agent + the guardian); the guardian does
wedge-detection and the in-process scheduler does periodic work. So
these backends are deliberately minimal — no timers, no health logic,
just "keep this alive / restart this."

Command CONSTRUCTION is unit-tested here; command EXECUTION is proven
on real boxes in the per-OS stress campaigns.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from windyfly.platform import IS_LINUX, IS_MAC, IS_WINDOWS


@dataclass
class ServiceUnit:
    name: str                       # e.g. "windy-guardian"
    description: str
    exec_args: list[str]            # full argv, e.g. ["uv","run","python","-m","..."]
    working_dir: str
    env: dict[str, str] = field(default_factory=dict)
    autostart: bool = True          # start at boot/login


class SupervisorBackend:
    """Base — subclasses build OS-specific commands. `_run` executes."""

    name = "base"

    def _run(self, args: list[str], timeout: float = 20.0) -> tuple[int, str]:
        try:
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except FileNotFoundError:
            return 127, f"not found: {args[0]}"
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        except Exception as e:  # noqa: BLE001
            return 1, str(e)

    # Subclasses override these four:
    def restart_command(self, name: str) -> list[str]: raise NotImplementedError
    def is_active_command(self, name: str) -> list[str]: raise NotImplementedError
    def install(self, unit: ServiceUnit) -> bool: raise NotImplementedError
    def uninstall(self, name: str) -> bool: raise NotImplementedError

    def restart(self, name: str) -> bool:
        code, _ = self._run(self.restart_command(name))
        return code == 0

    def is_active(self, name: str) -> bool:
        code, out = self._run(self.is_active_command(name))
        return self._parse_active(code, out)

    def _parse_active(self, code: int, out: str) -> bool:
        return code == 0


# ── Linux: systemd user units ────────────────────────────────────────
class SystemdBackend(SupervisorBackend):
    name = "systemd"

    def restart_command(self, name: str) -> list[str]:
        return ["systemctl", "--user", "restart", self._unit(name)]

    def is_active_command(self, name: str) -> list[str]:
        return ["systemctl", "--user", "is-active", self._unit(name)]

    def _unit(self, name: str) -> str:
        return name if name.endswith(".service") else f"{name}.service"

    def _parse_active(self, code: int, out: str) -> bool:
        return out.strip().startswith("active")

    def install(self, unit: ServiceUnit) -> bool:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        exec_line = " ".join(unit.exec_args)
        env_lines = "\n".join(
            f"Environment={k}={v}" for k, v in unit.env.items()
        )
        content = (
            f"[Unit]\nDescription={unit.description}\n"
            "After=network-online.target\n\n[Service]\n"
            f"WorkingDirectory={unit.working_dir}\n"
            f"ExecStart={exec_line}\n{env_lines}\n"
            "Restart=always\nRestartSec=10\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        (unit_dir / f"{unit.name}.service").write_text(content)
        self._run(["systemctl", "--user", "daemon-reload"])
        if unit.autostart:
            code, _ = self._run(
                ["systemctl", "--user", "enable", "--now", f"{unit.name}.service"]
            )
            return code == 0
        return True

    def uninstall(self, name: str) -> bool:
        self._run(["systemctl", "--user", "disable", "--now", self._unit(name)])
        p = Path.home() / ".config" / "systemd" / "user" / f"{name}.service"
        p.unlink(missing_ok=True)
        self._run(["systemctl", "--user", "daemon-reload"])
        return True


# ── Mac: launchd agents ──────────────────────────────────────────────
class LaunchdBackend(SupervisorBackend):
    name = "launchd"

    def _label(self, name: str) -> str:
        return name if name.startswith("com.windyfly.") else f"com.windyfly.{name}"

    def _plist_path(self, name: str) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self._label(name)}.plist"

    def restart_command(self, name: str) -> list[str]:
        # kickstart -k restarts the service (SIGKILL then relaunch).
        import os
        uid = os.getuid()
        return ["launchctl", "kickstart", "-k", f"gui/{uid}/{self._label(name)}"]

    def is_active_command(self, name: str) -> list[str]:
        return ["launchctl", "list", self._label(name)]

    def install(self, unit: ServiceUnit) -> bool:
        prog = "".join(
            f"        <string>{a}</string>\n" for a in unit.exec_args
        )
        envd = "".join(
            f"        <key>{k}</key><string>{v}</string>\n"
            for k, v in unit.env.items()
        )
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<plist version="1.0"><dict>\n'
            f"  <key>Label</key><string>{self._label(unit.name)}</string>\n"
            f"  <key>ProgramArguments</key><array>\n{prog}  </array>\n"
            f"  <key>WorkingDirectory</key><string>{unit.working_dir}</string>\n"
            f"  <key>RunAtLoad</key><{'true' if unit.autostart else 'false'}/>\n"
            "  <key>KeepAlive</key><true/>\n"
            f"  <key>EnvironmentVariables</key><dict>\n{envd}  </dict>\n"
            "</dict></plist>\n"
        )
        p = self._plist_path(unit.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist)
        code, _ = self._run(["launchctl", "load", "-w", str(p)])
        return code == 0

    def uninstall(self, name: str) -> bool:
        p = self._plist_path(name)
        self._run(["launchctl", "unload", "-w", str(p)])
        p.unlink(missing_ok=True)
        return True


# ── Windows: Task Scheduler (no-admin, grandma-installable) ───────────
class WindowsTaskSchedulerBackend(SupervisorBackend):
    """Task Scheduler backend. Chosen as the Windows DEFAULT (Grant
    2026-07-18): per-user, no admin needed. A Windows SERVICE (truer
    daemon, survives logout) is the documented power-user upgrade.

    Keep-alive: the task starts the process ONLOGON and Task Scheduler
    restarts it on failure (RestartCount/RestartInterval in the XML).
    The guardian is what this backend most needs to keep alive; the
    guardian in turn keeps the agent alive.
    """
    name = "windows-taskschd"

    def _tn(self, name: str) -> str:
        # Task path under a Windy folder for tidy grouping.
        return name if name.startswith("\\Windy\\") else f"\\Windy\\{name}"

    def restart_command(self, name: str) -> list[str]:
        # End then Run — schtasks has no atomic restart.
        return ["schtasks", "/Run", "/TN", self._tn(name)]

    def stop_command(self, name: str) -> list[str]:
        return ["schtasks", "/End", "/TN", self._tn(name)]

    def is_active_command(self, name: str) -> list[str]:
        return ["schtasks", "/Query", "/TN", self._tn(name), "/FO", "LIST"]

    def _parse_active(self, code: int, out: str) -> bool:
        return code == 0 and "Running" in out

    def restart(self, name: str) -> bool:
        self._run(self.stop_command(name))
        code, _ = self._run(self.restart_command(name))
        return code == 0

    def _task_xml(self, unit: ServiceUnit) -> str:
        # Minimal ONLOGON task with restart-on-failure. LIMITED run
        # level = no elevation (grandma default). %-quote the argv.
        cmd = unit.exec_args[0]
        args = " ".join(unit.exec_args[1:])
        return (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" '
            'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            f"  <RegistrationInfo><Description>{unit.description}</Description>"
            "</RegistrationInfo>\n"
            "  <Triggers><LogonTrigger><Enabled>"
            f"{'true' if unit.autostart else 'false'}</Enabled></LogonTrigger>"
            "</Triggers>\n"
            "  <Settings>\n"
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
            "    <RestartOnFailure><Interval>PT1M</Interval>"
            "<Count>999</Count></RestartOnFailure>\n"
            "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
            "  </Settings>\n"
            "  <Actions>\n"
            f"    <Exec><Command>{cmd}</Command><Arguments>{args}</Arguments>"
            f"<WorkingDirectory>{unit.working_dir}</WorkingDirectory></Exec>\n"
            "  </Actions>\n"
            "</Task>\n"
        )

    def install(self, unit: ServiceUnit) -> bool:
        import tempfile
        xml = self._task_xml(unit)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", delete=False, encoding="utf-16",
        ) as f:
            f.write(xml)
            xml_path = f.name
        code, _ = self._run([
            "schtasks", "/Create", "/TN", self._tn(unit.name),
            "/XML", xml_path, "/F",
        ])
        try:
            Path(xml_path).unlink(missing_ok=True)
        except Exception:
            pass
        return code == 0

    def uninstall(self, name: str) -> bool:
        code, _ = self._run(
            ["schtasks", "/Delete", "/TN", self._tn(name), "/F"]
        )
        return code == 0


def get_backend() -> SupervisorBackend:
    """The keep-alive backend for this OS."""
    if IS_WINDOWS:
        return WindowsTaskSchedulerBackend()
    if IS_MAC:
        return LaunchdBackend()
    if IS_LINUX:
        return SystemdBackend()
    raise RuntimeError("unsupported OS for supervisor backend")
