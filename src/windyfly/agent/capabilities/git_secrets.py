"""Which git config files carry a credential (Hub on #755 / #761). ONE check, shared by the shell sandbox's masks
(sandbox/docker.py) and, next, the file tools, so the two can't drift.

A git config file is a file named ``config`` or ``config.worktree`` whose folder holds a ``HEAD``: a repo's .git, a
bare or mirror clone (``mirror.git/config``), a submodule's ``.git/modules/<name>``, a linked worktree's
``.git/worktrees/<name>``. It carries a credential when it holds a token or password in a URL (``https://TOKEN@host``,
any ``scheme://user:password@host``, also inside a ``[url "..."]`` header) or an http extraheader (an Authorization
header). A plain ``ssh://git@host``, ``git@host:repo`` or ``https://host`` remote has no secret.

Not covered: files a config includes (``[include] path = ...``), hooks, and anything else under .git.
"""

from __future__ import annotations

import re
from pathlib import Path

GIT_CONFIG_NAMES = frozenset({"config", "config.worktree"})

_CRED = re.compile(
    r"""(?i)\b(?:https?|ftps?)://[^/\s@"'\]]+@"""            # any user part on http(s)/ftp(s): a token
    r"""|\b[a-z][a-z0-9+.-]*://[^/\s@:"'\]]*:[^/\s@"'\]]*@"""  # user:password on any scheme
    r"""|^\s*extraheader\s*=""",                              # http.extraheader (an Authorization header)
    re.MULTILINE,
)
MAX_BYTES = 1_000_000


def is_git_config(path: Path) -> bool:
    """Is ``path`` (by name and place, not followed) a git config file: ``config``/``config.worktree`` next to a HEAD?"""
    return path.name in GIT_CONFIG_NAMES and (path.parent / "HEAD").exists()


def has_credential(path: Path) -> bool:
    """Does this config carry a credential? One that can't be read whole counts as yes (fail closed)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return True
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    return _CRED.search(text) is not None


def git_dir_entries(git_dir: Path) -> tuple[list[Path], list[Path]]:
    """Inside a .git folder (which a walk does not enter): (the config files to check, the sub-git-folders that are
    symlinks). Config files: its own config + config.worktree, each submodule's (``modules/<name>``, one level) and each
    linked worktree's (``worktrees/<name>``). Nothing here is followed; the caller decides about symlinks."""
    files: list[Path] = [git_dir / n for n in sorted(GIT_CONFIG_NAMES)]
    links: list[Path] = []
    for sub in ("modules", "worktrees"):
        holder = git_dir / sub
        if holder.is_symlink():
            links.append(holder)
            continue
        if not holder.is_dir():
            continue
        for d in sorted(holder.iterdir()):
            if d.is_symlink():
                links.append(d)
            elif d.is_dir():
                files += [d / n for n in sorted(GIT_CONFIG_NAMES)]
    return [f for f in files if f.is_file() or f.is_symlink()], links
