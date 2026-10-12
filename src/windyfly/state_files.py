"""Private files in the agent's state dir (Hub 10-12, P5.78.4 / P5.104.5).

A file the agent keeps for itself (the OAuth token cache, the offline queue) lives in the state
dir (WINDY_STATE_DIR, default ~/.windy) unless its own env var names a path, never relative to the
working directory. It is written atomically through a temp file CREATED 0600, so it is never
readable by others, not even for a moment before a chmod. A file left at its old place
(``data/<name>`` under the project root or the working directory, like the provider salvage) is
moved once into the state dir; the move is logged without the file's contents.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from windyfly.platform import get_project_root, windy_state_dir

logger = logging.getLogger(__name__)


def state_path(env_var: str, name: str) -> Path:
    """The path named by ``env_var``, else ``name`` in the state dir."""
    raw = os.environ.get(env_var, "").strip()
    return Path(raw) if raw else windy_state_dir() / name


def write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, through a temp file created 0600."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()                       # a temp file left by a crash may have other modes
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def legacy_places(name: str) -> list[Path]:
    """Where an older Windy Fly kept ``name``: data/ under the project root, then under the cwd."""
    places = [get_project_root() / "data" / name, Path.cwd() / "data" / name]
    return [p for i, p in enumerate(places) if p not in places[:i]]


def move_legacy(name: str, new: Path, what: str) -> bool:
    """Move a file left at its old place into ``new`` (0600), once. Never overwrites ``new``."""
    if new.exists():
        return False
    old = next((p for p in legacy_places(name) if p.is_file()), None)
    if old is None:
        return False
    try:
        write_private(new, old.read_bytes())
        old.unlink()
    except OSError as exc:
        logger.warning("Could not move the %s into the state directory (%s)", what, type(exc).__name__)
        return False
    logger.info("Moved the %s into the state directory", what)
    return True
