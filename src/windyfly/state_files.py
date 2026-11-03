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


def _on_windows() -> bool:
    return os.name == "nt"


def _windows_account() -> str:
    """DOMAIN\\user for the current Windows account (a bare name is ambiguous on a domain or a renamed machine)."""
    user = os.environ.get("USERNAME", "").strip()
    domain = os.environ.get("USERDOMAIN", "").strip()
    return f"{domain}\\{user}" if user and domain else user


def _icacls(path: Path, grant: str) -> bool:
    import subprocess

    account = _windows_account()
    if not account:
        logger.warning("cannot restrict %s to its owner: USERNAME is unset", path)
        return False
    try:
        out = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:{grant}"],
                             capture_output=True, text=True, timeout=15, check=False)
    except Exception as exc:  # noqa: BLE001  (never block the write)
        logger.warning("icacls could not restrict %s (%s)", path, type(exc).__name__)
        return False
    if out.returncode != 0:
        logger.warning("icacls could not restrict %s (exit %d)", path, out.returncode)
        return False
    return True


def restrict_to_owner(path: Path) -> bool:
    """Make one file readable and writable by its owner ONLY, on every platform (#687 F, the Windows ACL).

    POSIX: mode 0600. Windows: os.chmod only toggles read-only (NTFS ACLs are the control there), so the inherited
    ACEs are dropped and exactly the current account (DOMAIN\\user) gets full control. Call it on the temp file
    BEFORE the rename: a rename keeps the file's ACL, so a credential is never readable by others, even for a moment.
    Best effort: a failure is logged, never raised (the agent must still be able to save its credentials)."""
    try:
        os.chmod(path, 0o600)
    except OSError as exc:  # pragma: no cover - platform dependent
        logger.warning("chmod 600 failed on %s (%s)", path, type(exc).__name__)
    return _icacls(path, "F") if _on_windows() else True


_private_dirs: set[str] = set()
_skipped_dirs: set[str] = set()


def _agent_owns(path: Path) -> bool:
    """The agent's own state root (WINDY_STATE_DIR or ~/.windy) or a directory under it."""
    try:
        root = windy_state_dir().resolve()
        target = path.resolve()
    except OSError:
        return False
    return target == root or root in target.parents


def ensure_private_dir(path: Path) -> bool:
    """Create ``path`` 0700, and return True when files created inside it are owner-only.

    POSIX: always True (the files are created 0600). Windows: give the directory an owner-only, INHERITABLE ACL once
    per process, so files inside inherit it (no icacls per write). But ONLY for a directory the agent owns: its state
    root or a directory under it, or one this call just created (Hub on #697). An EXISTING directory elsewhere (an
    env var can point a file anywhere, e.g. Documents) is never rewritten: stripping inheritance there would cut SYSTEM,
    Administrators and backup out of the whole tree. Then the caller restricts the file itself."""
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not _on_windows():
        return True
    if str(path) in _private_dirs:
        return True
    if existed and not _agent_owns(path):
        if str(path) not in _skipped_dirs:
            _skipped_dirs.add(str(path))
            logger.info("not hardening %s: an existing folder the agent does not own (its files are restricted "
                        "one by one)", path)
        return False
    if _icacls(path, "(OI)(CI)F"):
        _private_dirs.add(str(path))
        return True
    return False


def write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, through a temp file created 0600 in an owner-only directory."""
    dir_private = ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()                       # a temp file left by a crash may have other modes
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if not dir_private:
        restrict_to_owner(tmp)  # a folder the agent may not harden (Windows): restrict the file itself, still empty
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
