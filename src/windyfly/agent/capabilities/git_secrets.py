"""Which git config files carry a credential (Hub on #755 / #761). ONE check, shared by the shell sandbox's masks
(sandbox/docker.py) and the file tools (filesystem.py), so the two can't drift.

A git config file is a file named ``config`` or ``config.worktree`` whose folder holds a ``HEAD`` (a repo's .git, a
bare or mirror clone ``mirror.git/config``, a submodule's ``.git/modules/<name>``, a linked worktree's
``.git/worktrees/<name>``), a user-level git config by its name (``.gitconfig`` and its variants,
``~/.config/git/config``), or a file the global configs include (one level). It carries a credential when it holds a
token or password in a URL (``https://TOKEN@host``, any ``scheme://user:password@host``, also inside a
``[url "..."]`` header) or an http extraheader (an Authorization header). A plain ``ssh://git@host``, ``git@host:repo`` or ``https://host`` remote has no secret.

A repo config's own includes are checked by the shell sandbox (it reads each repo config it passes); the file tools
can't know a file is included by some repo without reading every repo's config, so for them only the global configs'
includes count. Not covered: includes of includes, hooks, and anything else under .git.
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


_INCLUDE_PATH = re.compile(r"""^\s*path\s*=\s*["']?([^"'\n]+?)["']?\s*$""", re.MULTILINE | re.IGNORECASE)
_include_cache: dict[tuple[tuple[str, float], ...], frozenset[Path]] = {}


def _global_configs() -> list[Path]:
    home = Path.home()
    return [home / ".gitconfig", home / ".config" / "git" / "config"]


def include_targets(cfg: Path) -> list[Path]:
    """The files ONE git config includes ([include] / [includeIf] ``path = ...``), resolved (``~`` and relative to the
    config's folder). One level: what those files include is not followed. Only WHICH files; never whether they hold a
    credential (that is checked on every read, Hub on #761)."""
    try:
        if cfg.stat().st_size > MAX_BYTES:
            return []
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in _INCLUDE_PATH.findall(text):
        target = Path(raw.strip()).expanduser()
        out.append((target if target.is_absolute() else cfg.parent / target).resolve(strict=False))
    return out


def global_include_targets() -> frozenset[Path]:
    """The files the user's global git configs include ([include] / [includeIf] ``path = ...``, one level): a token
    can sit in any of them under any name (Boss on #761). Cached while the global configs are unchanged."""
    key = []
    for cfg in _global_configs():
        try:
            key.append((str(cfg), cfg.stat().st_mtime))
        except OSError:
            key.append((str(cfg), -1.0))
    k = tuple(key)
    if k not in _include_cache:
        found: set[Path] = set()
        for cfg in _global_configs():
            found.update(include_targets(cfg))
        _include_cache.clear()
        _include_cache[k] = frozenset(found)
    return _include_cache[k]


def _global_name(path: Path) -> bool:
    """A user-level git config by its name: .gitconfig and its variants (.gitconfig-work, .gitconfig.local,
    work.gitconfig) and ~/.config/git/config."""
    n = path.name.lower()
    if n == ".gitconfig" or n.startswith(".gitconfig") or n.endswith(".gitconfig"):
        return True
    return n == "config" and path.parent.name == "git" and path.parent.parent.name == ".config"


def is_git_config(path: Path) -> bool:
    """Is ``path`` a git config file? By name and place (not followed): ``config``/``config.worktree`` next to a HEAD,
    a user-level git config by its name, or a file the global configs include."""
    if path.name in GIT_CONFIG_NAMES and (path.parent / "HEAD").exists():
        return True
    if _global_name(path):
        return True
    targets = global_include_targets()
    return bool(targets) and path.resolve(strict=False) in targets


def in_git_dir(path: Path) -> bool:
    """Is ``path`` a file directly inside a git folder (one that holds a HEAD), e.g. a renamed ``config.real``?"""
    return path.name != "HEAD" and (path.parent / "HEAD").exists()


def has_credential(path: Path, *, fail_closed: bool = True) -> bool:
    """Does this file carry a credential? One that can't be read whole counts as yes unless ``fail_closed`` is off
    (used for files that are not git configs by name, so a big binary file in .git isn't refused for its size)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return fail_closed
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fail_closed
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
