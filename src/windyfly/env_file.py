"""Write one KEY=value line into the agent's env file, atomically, readable only by its owner.

Moved out of the setup.* capability (cut in the lean Windy Fly, 2026-11-02); provider_salvage still uses it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _atomic_upsert_env_var(
    env_file: Path, var_name: str, value: str,
) -> None:
    """Add or replace ``var_name=value`` in the env file, atomically.

    If the line already exists, replace it. Otherwise append. Uses
    temp + rename so a crash mid-write can't leave the file in a
    half-state. File is chmod 600 (existing or fresh).

    Symlink-aware: when ``env_file`` is a symlink to an existing real
    file, we resolve and write through the link to the real target —
    naive ``os.replace`` against a symlink path would atomically
    REPLACE the symlink with a regular file, silently breaking
    multi-machine fleet setups where the env file is shared via
    link. Found via industrial hardening probe 2026-04-27.
    """
    # Resolve symlinks so the temp+rename happens at the REAL target
    # path. .resolve(strict=False) handles non-existent paths
    # gracefully and follows arbitrary chains. For non-symlinks this
    # returns the absolute path of env_file unchanged.
    target = env_file
    if env_file.is_symlink() and env_file.exists():
        target = env_file.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    new_line = f"{var_name}={value}\n"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    pattern = re.compile(rf"^{re.escape(var_name)}=.*\n?", re.MULTILINE)
    if pattern.search(existing):
        new_text = pattern.sub(new_line, existing, count=1)
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_text = existing + new_line
    tmp = target.with_suffix(target.suffix + ".windy.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    _restrict_to_owner(tmp)
    os.replace(tmp, target)


def _restrict_to_owner(path: Path) -> None:
    """Make ``path`` readable only by its owner, on any platform.

    This file holds live credentials, so "we chmod 600 it" needs to be
    TRUE, not just written down.

    ``os.chmod(0o600)`` is a POSIX statement. On Windows it only
    toggles the read-only attribute — the mode bits are not the access
    control mechanism there, NTFS ACLs are — so the file came out
    reporting ``0o666`` and inheriting whatever ACL its parent had.
    Caught on the GrantW Windows 11 box: ``assert 438 == 384``
    (``0o666`` vs ``0o600``).

    In practice a file under ``%USERPROFILE%`` inherits an ACL that
    already excludes other standard users — but it does NOT exclude
    other Administrators, and "probably fine by inheritance" is not a
    guarantee to hand a credential. So on Windows we drop inheritance
    and grant exactly the current user.

    Best-effort by design: a failure here must never block the write
    (the alternative is the agent unable to save a token at all), but
    it is logged rather than swallowed silently.
    """
    try:
        os.chmod(path, 0o600)
    except OSError as exc:  # pragma: no cover - platform dependent
        logger.warning("chmod 600 failed on %s: %s", path, exc)

    if os.name != "nt":
        return

    # /inheritance:r drops inherited ACEs; /grant:r replaces any
    # existing grant for that principal rather than adding to it.
    user = os.environ.get("USERNAME") or ""
    if not user:
        logger.warning("cannot restrict %s: USERNAME unset", path)
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", f"{user}:F"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - never block the write
        logger.warning("icacls hardening failed on %s: %s", path, exc)
