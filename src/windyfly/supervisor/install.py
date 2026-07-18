"""Install the supervisor on the current OS (the last-mile wiring).

'Unify on the guardian model' (Grant 2026-07-18): on every OS we
install the SAME two things via the OS keep-alive backend —
  1. the agent (one unit per channel), kept alive by the OS;
  2. the guardian, kept alive by the OS, which in turn watches the
     agent for wedges the OS alone can't see.
The in-process maintenance scheduler (journal, drills) rides inside the
agent, so there are NO OS timers to install anywhere.

Unit CONSTRUCTION is testable here; the install() calls execute through
the OS backend (proven per-OS in the recovery campaigns).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from windyfly.supervisor.backends import ServiceUnit, SupervisorBackend, get_backend


def _uv() -> str:
    return shutil.which("uv") or "uv"


def build_units(
    *,
    project_root: Path,
    channels: list[str] | None = None,
    config_path: str | None = None,
    instance: str = "windy",
) -> list[ServiceUnit]:
    """The units to install: one agent unit per channel + the guardian."""
    channels = channels or ["telegram", "matrix"]
    uv = _uv()
    wd = str(project_root)
    env: dict[str, str] = {}
    if config_path:
        env["WINDYFLY_CONFIG"] = config_path

    units: list[ServiceUnit] = []
    for ch in channels:
        units.append(ServiceUnit(
            name=f"{instance}-agent-{ch}",
            description=f"Windy Fly agent ({ch})",
            exec_args=[uv, "run", "python", "-m", "windyfly.main",
                       "--channel", ch],
            working_dir=wd,
            env=dict(env),
            autostart=True,
        ))
    # The guardian watches every agent unit it's told about.
    unit_names = " ".join(u.name for u in units)
    units.append(ServiceUnit(
        name=f"{instance}-guardian",
        description="Windy Fly guardian — wedge/crash watchdog",
        exec_args=[uv, "run", "python", "-m", "windyfly.supervisor.run_guardian"],
        working_dir=wd,
        env={**env, "WINDY_GUARDIAN_UNITS": unit_names,
             "WINDY_GUARDIAN_CHANNELS": " ".join(channels)},
        autostart=True,
    ))
    return units


def install_supervisor(
    *,
    project_root: Path,
    channels: list[str] | None = None,
    config_path: str | None = None,
    backend: SupervisorBackend | None = None,
) -> dict[str, bool]:
    """Install agent + guardian units on this OS. Returns {name: ok}."""
    backend = backend or get_backend()
    units = build_units(
        project_root=project_root, channels=channels, config_path=config_path,
    )
    results: dict[str, bool] = {}
    for unit in units:
        try:
            results[unit.name] = bool(backend.install(unit))
        except Exception:
            results[unit.name] = False
    return results


def uninstall_supervisor(
    *,
    channels: list[str] | None = None,
    instance: str = "windy",
    backend: SupervisorBackend | None = None,
) -> dict[str, bool]:
    backend = backend or get_backend()
    channels = channels or ["telegram", "matrix"]
    names = [f"{instance}-agent-{ch}" for ch in channels] + [f"{instance}-guardian"]
    out: dict[str, bool] = {}
    for name in names:
        try:
            out[name] = bool(backend.uninstall(name))
        except Exception:
            out[name] = False
    return out
