"""Which env file an agent process reads (Boss BOARD 2026-11-07T16:13:24Z, option B).

1. ``WINDY_ENV_FILE`` when it is set: that file and nothing else.
2. Otherwise the project ``.env`` ONLY when this agent's own config file sits in that same project folder (not a
   subfolder, not elsewhere): a normal
   ``windy go`` / pip install, where the folder, its config and its ``.env`` belong to one agent. One log line says
   so (names only), so we can count who still relies on it.
3. Never a bare ``load_dotenv()``: it searches upward from wherever the code sits, so a second agent run from the same
   code checkout with its own config elsewhere (a lab agent) read the first agent's ``.env`` (finding of 2026-11-05).

Nothing here overrides a variable that is already set (systemd's EnvironmentFile wins).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_said_implicit = False


def _project_root() -> Path:
    from windyfly.platform import get_project_root

    return get_project_root()


def _config_file(config_path: str | os.PathLike[str] | None) -> Path:
    raw = str(config_path) if config_path else os.environ.get("WINDYFLY_CONFIG", "windyfly.toml")
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else Path.cwd() / p).resolve()


def load_agent_env(config_path: str | os.PathLike[str] | None = None) -> Path | None:
    """Load this agent's env file by the rules above; return the file read, or None."""
    global _said_implicit
    explicit = os.environ.get("WINDY_ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            load_dotenv(path, override=False)
            return path
        logger.warning("env: WINDY_ENV_FILE names a file that does not exist; no env file read")
        return None
    root = _project_root().resolve()
    project_env = root / ".env"
    if not project_env.is_file():
        return None
    if _config_file(config_path).parent != root:
        return None          # this agent's config is not in the project folder itself: the .env is not its own
    load_dotenv(project_env, override=False)
    if not _said_implicit:
        _said_implicit = True
        logger.info("env: read the project .env implicitly (its config is in the same folder); "
                    "set WINDY_ENV_FILE to name it")
    return project_env


def _atomic_write(path: Path, data: bytes) -> None:
    """Write a temp file next to ``path``, keep its mode, then rename over it (never a half-written service file)."""
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def systemd_quote(value: str) -> str:
    """One systemd Environment= assignment value, quoted: % doubled (specifiers), backslash and quote escaped."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _unit_env_file(text: str) -> Path | None:
    """The env file a systemd unit ITSELF names: its EnvironmentFile=, else WorkingDirectory=/.env if that exists."""
    env_file = workdir = None
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        if key == "EnvironmentFile" and value:
            env_file = Path(value.lstrip("-").strip().strip('"'))
        elif key == "WorkingDirectory" and value:
            workdir = Path(value.strip().strip('"'))
    if env_file is not None:
        return env_file
    if workdir is not None and (workdir / ".env").is_file():
        return workdir / ".env"
    return None


def name_env_file_in_services() -> list[Path]:
    """Make an EXISTING service file name its env file (run by ``windy update``); return the files changed.

    The path always comes from the service file ITSELF (the plist's WorkingDirectory; the unit's EnvironmentFile= or
    WorkingDirectory=), never from the checkout ``windy update`` runs in: an update run from a lab checkout must not
    point the real agent's service at the lab's keys (Hub on #754). Only files that lack the setting are touched, only
    when that env file exists, and every write is a temp file + rename.
    """
    import plistlib

    changed: list[Path] = []
    plist = Path.home() / "Library" / "LaunchAgents" / "com.windyfly.agent.plist"
    if plist.is_file():
        try:
            data = plistlib.loads(plist.read_bytes())
        except Exception:  # noqa: BLE001 - an unreadable plist is left alone
            data = None
        if isinstance(data, dict):
            env = data.setdefault("EnvironmentVariables", {})
            workdir = data.get("WorkingDirectory")
            if isinstance(env, dict) and "WINDY_ENV_FILE" not in env and isinstance(workdir, str):
                own = Path(workdir) / ".env"
                if own.is_file():
                    env["WINDY_ENV_FILE"] = str(own)
                    _atomic_write(plist, plistlib.dumps(data))
                    changed.append(plist)
    unit = Path.home() / ".config" / "systemd" / "user" / "windyfly.service"
    if unit.is_file():
        text = unit.read_text(encoding="utf-8")
        unit_env = _unit_env_file(text)
        if "WINDY_ENV_FILE=" not in text and "\n[Service]\n" in text and unit_env is not None and unit_env.is_file():
            line = f'Environment="WINDY_ENV_FILE={systemd_quote(str(unit_env))}"'
            _atomic_write(unit, text.replace("\n[Service]\n", f"\n[Service]\n{line}\n", 1).encode("utf-8"))
            changed.append(unit)
    return changed
