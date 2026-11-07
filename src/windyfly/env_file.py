"""Which env file an agent process reads (Boss BOARD 2026-11-07T16:13:24Z, option B).

1. ``WINDY_ENV_FILE`` when it is set: that file and nothing else.
2. Otherwise the project ``.env`` ONLY when this agent's own config file lives in that same project folder: a normal
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
    if not _config_file(config_path).is_relative_to(root):
        return None          # this agent's config lives elsewhere: the project .env is not its own
    load_dotenv(project_env, override=False)
    if not _said_implicit:
        _said_implicit = True
        logger.info("env: read the project .env implicitly (its config is in the same folder); "
                    "set WINDY_ENV_FILE to name it")
    return project_env


_PLIST_KEY = "<key>WINDY_ENV_FILE</key>"


def name_env_file_in_services() -> list[Path]:
    """Make an EXISTING service file name its env file (run by ``windy update``); return the files changed.

    macOS: the launchd plist ``windy`` installs set only PATH, so its agent read the project .env implicitly; it gets a
    WINDY_ENV_FILE entry. Linux: the systemd unit already passes the file as EnvironmentFile=; it gets the same name
    as Environment=. Only files that lack it are touched, and only when the project .env exists.
    """
    env_file = _project_root().resolve() / ".env"
    if not env_file.is_file():
        return []
    changed: list[Path] = []
    plist = Path.home() / "Library" / "LaunchAgents" / "com.windyfly.agent.plist"
    if plist.is_file():
        text = plist.read_text(encoding="utf-8")
        anchor = "<key>EnvironmentVariables</key>\n    <dict>\n"
        if _PLIST_KEY not in text and anchor in text:
            entry = f"        {_PLIST_KEY}\n        <string>{env_file}</string>\n"
            plist.write_text(text.replace(anchor, anchor + entry, 1), encoding="utf-8")
            changed.append(plist)
    unit = Path.home() / ".config" / "systemd" / "user" / "windyfly.service"
    if unit.is_file():
        text = unit.read_text(encoding="utf-8")
        if "WINDY_ENV_FILE=" not in text and "\n[Service]\n" in text:
            unit.write_text(text.replace("\n[Service]\n", f"\n[Service]\nEnvironment=WINDY_ENV_FILE={env_file}\n", 1),
                            encoding="utf-8")
            changed.append(unit)
    return changed
