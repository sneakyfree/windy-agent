"""Cross-platform abstraction layer for Windy Fly.

Every OS-specific operation lives here so the rest of the codebase stays
platform-agnostic.  Import from this module instead of calling ``os.kill``,
``pkill``, or hardcoding ``/tmp/`` paths directly.

Supported platforms:
    - macOS  (darwin)
    - Linux  (linux)
    - Windows (win32) — via TCP IPC fallback + native process management

Environment overrides:
    WINDYFLY_IPC_MODE   — force "uds" or "tcp" (auto-detected if unset)
    WINDYFLY_IPC_PATH   — custom UDS socket path (default: <tempdir>/windyfly.sock)
    WINDYFLY_IPC_HOST   — TCP host (default: 127.0.0.1)
    WINDYFLY_IPC_PORT   — TCP port (default: 9119)
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ── Platform detection ────────────────────────────────────────────────

SYSTEM: str = platform.system().lower()  # "darwin", "linux", "windows"
IS_WINDOWS: bool = SYSTEM == "windows"
IS_MAC: bool = SYSTEM == "darwin"
IS_LINUX: bool = SYSTEM == "linux"
IS_POSIX: bool = not IS_WINDOWS


# ── IPC configuration ────────────────────────────────────────────────

IPCMode = Literal["uds", "tcp"]

# Default TCP settings for Windows (or when forced via env)
_DEFAULT_TCP_HOST = "127.0.0.1"
_DEFAULT_TCP_PORT = 4001


def get_ipc_mode() -> IPCMode:
    """Return the IPC mode for this platform.

    UDS on Mac/Linux, TCP on Windows.  Override with WINDYFLY_IPC_MODE.
    """
    override = os.environ.get("WINDYFLY_IPC_MODE", "").lower()
    if override in ("uds", "tcp"):
        return override  # type: ignore[return-value]
    return "uds" if IS_POSIX else "tcp"


def get_ipc_path() -> str:
    """Return the UDS socket path.  Only meaningful when ``get_ipc_mode() == "uds"``.

    Default: ``<tempdir>/windyfly.sock``
    Override: WINDYFLY_IPC_PATH
    """
    override = os.environ.get("WINDYFLY_IPC_PATH")
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), "windyfly.sock")


def get_ipc_tcp_host() -> str:
    """Return the TCP host for IPC.  Override with WINDYFLY_IPC_HOST."""
    return os.environ.get("WINDYFLY_IPC_HOST", _DEFAULT_TCP_HOST)


def get_ipc_tcp_port() -> int:
    """Return the TCP port for IPC.  Override with WINDYFLY_IPC_PORT."""
    return int(os.environ.get("WINDYFLY_IPC_PORT", str(_DEFAULT_TCP_PORT)))


@dataclass(frozen=True)
class IPCConfig:
    """Resolved IPC configuration — pass this around instead of raw strings."""

    mode: IPCMode
    socket_path: str  # only used when mode == "uds"
    tcp_host: str     # only used when mode == "tcp"
    tcp_port: int     # only used when mode == "tcp"


def get_ipc_config() -> IPCConfig:
    """Build and return the resolved IPC configuration."""
    return IPCConfig(
        mode=get_ipc_mode(),
        socket_path=get_ipc_path(),
        tcp_host=get_ipc_tcp_host(),
        tcp_port=get_ipc_tcp_port(),
    )


# ── Process management ────────────────────────────────────────────────

def process_alive(pid: int) -> bool:
    """Check if a process is still running.  Cross-platform."""
    if IS_WINDOWS:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
            )
            return str(pid) in result.stdout
        except FileNotFoundError:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def process_terminate(pid: int) -> bool:
    """Terminate a process by PID.  Returns True if signal was sent.

    Uses SIGTERM on POSIX, taskkill on Windows.
    """
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
            )
            return True
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except (OSError, ProcessLookupError):
            return False


def kill_by_name(patterns: list[str]) -> None:
    """Kill processes matching any of the given command-line patterns.

    Uses ``pkill -f`` on POSIX, ``taskkill /IM`` on Windows.
    This is the fallback when PID files are missing.
    """
    if IS_WINDOWS:
        for pattern in patterns:
            # On Windows we try to match the process name
            # (less precise than pkill -f, but best available)
            try:
                subprocess.run(
                    ["taskkill", "/F", "/FI", f"IMAGENAME eq {pattern}*"],
                    capture_output=True,
                )
            except FileNotFoundError:
                pass
    else:
        for pattern in patterns:
            try:
                subprocess.run(
                    ["pkill", "-f", pattern],
                    capture_output=True,
                )
            except FileNotFoundError:
                pass


# ── Path helpers ──────────────────────────────────────────────────────

def get_project_root() -> Path:
    """Return the Windy Fly project/working directory.

    Resolution order:
      1. WINDYFLY_HOME env var (explicit override)
      2. Git repo root (if running from source checkout)
      3. Current working directory (pip-installed package)

    This handles both ``git clone`` + ``uv run`` development and
    ``pip install windyfly && windy go`` production use.
    """
    # 1. Explicit override
    home = os.environ.get("WINDYFLY_HOME")
    if home:
        return Path(home).resolve()

    # 2. Try to find a git repo or windyfly.toml upward from CWD
    cwd = Path.cwd()
    for marker in ("windyfly.toml", ".env", "pyproject.toml"):
        if (cwd / marker).exists():
            return cwd

    # 3. Check if __file__ is inside a source checkout (dev mode)
    source_root = Path(__file__).resolve().parent.parent.parent
    if (source_root / "pyproject.toml").exists():
        return source_root

    # 4. Default to CWD — pip-installed, first run
    return cwd


def get_temp_dir() -> Path:
    """Return the system temp directory as a Path."""
    return Path(tempfile.gettempdir())


def get_data_dir(project_root: Path) -> Path:
    """Return and ensure the data directory exists."""
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_log_path(project_root: Path, name: str) -> Path:
    """Return the log file path for a component (e.g., 'brain', 'gateway')."""
    return get_data_dir(project_root) / f"{name}.log"


def get_pid_path(project_root: Path) -> Path:
    """Return the PID file path."""
    return get_data_dir(project_root) / "windyfly.pid"


@dataclass
class PIDInfo:
    """Parsed PID file contents."""

    brain: int | None = None
    gateway: int | None = None
    started: str = ""

    @property
    def brain_alive(self) -> bool:
        return self.brain is not None and process_alive(self.brain)

    @property
    def gateway_alive(self) -> bool:
        return self.gateway is not None and process_alive(self.gateway)

    @property
    def any_alive(self) -> bool:
        return self.brain_alive or self.gateway_alive


def read_pid_file(project_root: Path) -> PIDInfo | None:
    """Read and parse the PID file. Returns None if file doesn't exist."""
    pid_path = get_pid_path(project_root)
    if not pid_path.exists():
        return None
    info = PIDInfo()
    try:
        for line in pid_path.read_text().strip().splitlines():
            if "=" not in line:
                # Legacy format: plain PID per line
                try:
                    pid = int(line.strip())
                    if info.brain is None:
                        info.brain = pid
                    elif info.gateway is None:
                        info.gateway = pid
                except ValueError:
                    pass
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key == "brain":
                info.brain = int(val)
            elif key == "gateway":
                info.gateway = int(val)
            elif key == "started":
                info.started = val
    except Exception as e:
        logger.debug("PID file parse failed: %s", e)
        return None
    return info


def write_pid_file(
    project_root: Path,
    brain_pid: int | None = None,
    gateway_pid: int | None = None,
) -> None:
    """Write the PID file in key=value format."""
    from datetime import datetime, timezone

    pid_path = get_pid_path(project_root)
    lines = []
    if brain_pid is not None:
        lines.append(f"brain={brain_pid}")
    if gateway_pid is not None:
        lines.append(f"gateway={gateway_pid}")
    lines.append(f"started={datetime.now(timezone.utc).isoformat()}")
    pid_path.write_text("\n".join(lines) + "\n")


def remove_pid_file(project_root: Path) -> None:
    """Remove the PID file if it exists."""
    get_pid_path(project_root).unlink(missing_ok=True)


def force_kill(pid: int) -> bool:
    """Force-kill a process (SIGKILL on POSIX, taskkill /F on Windows)."""
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            return True
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
    else:
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except (OSError, ProcessLookupError):
            return False


# ── Installer helpers ─────────────────────────────────────────────────

def get_shell() -> str:
    """Return the shell command for running install scripts."""
    if IS_WINDOWS:
        return "powershell"
    return "bash"


def can_run(cmd: str) -> bool:
    """Check if a command is available on PATH."""
    import shutil
    return shutil.which(cmd) is not None


# ── Capability report (used by `windy doctor`) ───────────────────────

@dataclass
class PlatformReport:
    """Snapshot of platform capabilities for diagnostics."""

    system: str = ""
    python_version: str = ""
    ipc_mode: IPCMode = "uds"
    has_uv: bool = False
    has_bun: bool = False
    has_git: bool = False
    has_bash: bool = False
    has_powershell: bool = False
    issues: list[str] = field(default_factory=list)


def diagnose() -> PlatformReport:
    """Run platform diagnostics and return a report.

    This is the foundation for ``windy doctor``.
    """
    report = PlatformReport(
        system=SYSTEM,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ipc_mode=get_ipc_mode(),
        has_uv=can_run("uv"),
        has_bun=can_run("bun"),
        has_git=can_run("git"),
        has_bash=can_run("bash"),
        has_powershell=can_run("powershell") or can_run("pwsh"),
    )

    # Check Python version
    if sys.version_info < (3, 12):
        report.issues.append(
            f"Python {report.python_version} detected — need 3.12+"
        )

    # Check required tools
    if not report.has_uv:
        report.issues.append("uv not found — install: https://docs.astral.sh/uv/")
    if not report.has_bun:
        report.issues.append("Bun not found — install: https://bun.sh")
    if not report.has_git:
        report.issues.append("Git not found — install: https://git-scm.com")

    # Platform-specific checks
    if IS_WINDOWS:
        if report.ipc_mode == "uds":
            report.issues.append(
                "UDS mode forced on Windows — this will fail. "
                "Set WINDYFLY_IPC_MODE=tcp or remove the override."
            )
        if not report.has_powershell:
            report.issues.append("PowerShell not found — needed for Windows setup")
    else:
        if not report.has_bash:
            report.issues.append("Bash not found — needed for install scripts")

    return report
