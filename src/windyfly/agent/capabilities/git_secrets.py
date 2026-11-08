"""Which git config files carry a credential (Hub on #755 / #761). ONE check, shared by the shell sandbox's masks
(sandbox/docker.py) and the file tools (filesystem.py), so the two can't drift.

A git config file is a file named ``config`` or ``config.worktree`` whose folder holds a ``HEAD`` (a repo's .git, a
bare or mirror clone ``mirror.git/config``, a submodule's ``.git/modules/<name>``, a linked worktree's
``.git/worktrees/<name>``), a user-level git config by its name (``.gitconfig`` and its variants,
``~/.config/git/config``), or a file the global configs include (one level). It carries a credential when it holds a
token or password in a URL (``https://TOKEN@host``, any ``scheme://user:password@host``, also inside a
``[url "..."]`` header) or an http extraheader (an Authorization header). A plain ``ssh://git@host``, ``git@host:repo`` or ``https://host`` remote has no secret.

Includes are followed the way git does, to a depth of 10 (includes of includes). A repo config's own includes are
checked by the shell sandbox (it reads each repo config it passes); the file tools can't know a file is included by some
repo without reading every repo's config, so for them only the global configs' includes count. A file directly inside a
git folder (a config renamed and linked) is scanned whole, however big. A hard link to a credentialed config: the file
tools check any file with more than one link (up to MAX_BYTES); the sandbox masks any file under the root that shares
(device, inode) with a credentialed config it found. Not covered: hooks and anything else under .git.
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
INCLUDE_DEPTH = 10            # git's own limit on nested includes
_CHUNK = 1 << 20
_OVERLAP = 64 * 1024          # longer than any token: a match split across two chunks is still seen


_INCLUDE_PATH = re.compile(r"""^\s*path\s*=\s*["']?([^"'\n]+?)["']?\s*$""", re.MULTILINE | re.IGNORECASE)
# One entry: (the (path, mtime) of the global configs AND every file they include, the include targets). Any of those
# changing (or appearing) makes it stale. It holds only WHICH files; content is read on every check (Hub on #761).
_include_cache: list[tuple[tuple[tuple[str, float], ...], frozenset[Path]]] = []


def _global_configs() -> list[Path]:
    home = Path.home()
    return [home / ".gitconfig", home / ".config" / "git" / "config"]


def _direct_includes(cfg: Path) -> list[Path]:
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


def include_targets(cfg: Path, *, depth: int = INCLUDE_DEPTH) -> list[Path]:
    """The files a git config includes ([include] / [includeIf] ``path = ...``), resolved (``~`` and relative to the
    including file's folder), and what THOSE include, to ``depth`` levels like git; a loop is followed once. Only
    WHICH files; never whether they hold a credential (that is checked on every read, Hub on #761)."""
    out: list[Path] = []
    seen: set[Path] = set()
    todo = [(cfg, 0)]
    while todo:
        cur, level = todo.pop()
        for target in _direct_includes(cur):
            if target in seen:
                continue
            seen.add(target)
            out.append(target)
            if level + 1 < depth:
                todo.append((target, level + 1))
    return out


def _stamp(paths: list[Path]) -> tuple[tuple[str, float], ...]:
    out = []
    for p in paths:
        try:
            out.append((str(p), p.stat().st_mtime))
        except OSError:
            out.append((str(p), -1.0))
    return tuple(out)


def global_include_targets() -> frozenset[Path]:
    """The files the user's global git configs include ([include] / [includeIf] ``path = ...``, nested like git): a
    token can sit in any of them under any name (Boss on #761). Cached while none of those files changes."""
    configs = _global_configs()
    if _include_cache:
        stamp, targets = _include_cache[0]
        same_home = [p for p, _ in stamp[: len(configs)]] == [str(c) for c in configs]
        if same_home and _stamp([Path(p) for p, _ in stamp]) == stamp:
            return targets
    found: set[Path] = set()
    for cfg in configs:
        found.update(include_targets(cfg))
    targets = frozenset(found)
    _include_cache[:] = [(_stamp([*configs, *sorted(targets)]), targets)]
    return targets


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


def _scan(path: Path) -> bool:
    """Does ``path`` hold a credential anywhere? Read in chunks, each searched with the tail of the one before."""
    tail = ""
    with path.open("rb") as fh:
        while block := fh.read(_CHUNK):
            text = tail + block.decode("utf-8", errors="replace")
            if _CRED.search(text):
                return True
            tail = text[-_OVERLAP:]
            tail = tail[tail.find("\n") + 1:]     # start at a line, so ^extraheader can't match mid-line
    return False


def has_credential(path: Path, *, fail_closed: bool = True, scan_all: bool = False) -> bool:
    """Does this file carry a credential? Past MAX_BYTES: yes when ``fail_closed`` (a git config by name), else
    scanned whole when ``scan_all`` (a file directly in a git folder, padded: Hub on #762), else no. A file that
    can't be read counts as ``fail_closed``."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return True if fail_closed else (scan_all and _scan(path))
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fail_closed
    return _CRED.search(text) is not None


# ── The content rule (Boss BOARD 2026-11-08T03:23:47Z): ANY file, whatever its name or place, whose text has a
# git-only section header at the start of a line AND a credential that is not a placeholder. This is what closes the
# file tools' repo-include reverse lookup (a file some repo's config includes, under any name) without reading every
# repo's config; it also covers hard links, renames and includes of includes. The owner can still read such a file
# from their own shell.
_GIT_HEADER = re.compile(r"(?im)^\s*\[\s*(?:remote|url|http|credential|submodule)\b")
# A README's https://<token>@host or ${GH_TOKEN} is not a credential (Hub on the 11-13 gaps).
_PLACEHOLDER = re.compile(
    r"""(?ix)^(?:<[^>]*>|\$\{[^}]*\}|\$[a-z_]\w*|%[a-z_]\w*%|\{\{[^}]*\}\}|x{3,}|\*+|\.{3,}
    |(?:your[_-]?)?(?:token|password|passwd|pass|secret|pat|key|api[_-]?key)(?:[_-]?here)?)$"""
)


# User names that are never the secret themselves (the token goes in the password): https://x-access-token:TOKEN@.
_KNOWN_USERS = frozenset({"git", "x-access-token", "oauth2", "x-token-auth", "token", "user", "username"})
# Words of an Authorization header that are not the secret.
_AUTH_WORDS = frozenset({"authorization:", "authorization", "bearer", "basic", "token", "digest"})


def _placeholder(word: str) -> bool:
    return _PLACEHOLDER.match(word) is not None


def _is_placeholder(match: re.Match[str], text: str) -> bool:
    """Is EVERY part of this _CRED match that could hold the secret a placeholder (Hub on #765)? For a URL both the
    user and the password count (a host accepts the token as the user with any password), the user may also be a
    known non-secret name; for an extraheader every word after the header and scheme words counts."""
    found = match.group(0)
    if found.rstrip().lower().endswith("="):                    # extraheader = <value>
        end = text.find("\n", match.end())
        value = text[match.end(): len(text) if end < 0 else end].strip().strip("\"'")
        words = [w for w in value.split() if w.lower() not in _AUTH_WORDS]
        return all(_placeholder(w) for w in words)
    userinfo = found.split("://", 1)[1].rstrip("@")
    user, sep, password = userinfo.partition(":")
    user_ok = _placeholder(user) or user.lower() in _KNOWN_USERS
    return user_ok and (not sep or _placeholder(password))


def _real_credential(text: str) -> bool:
    return any(not _is_placeholder(m, text) for m in _CRED.finditer(text))


def text_has_git_credential(text: str) -> bool:
    """The content rule, on text a tool has ALREADY read (no second read): a git-only section header at the start of a
    line and a real (not placeholder) credential, in the same text."""
    return _GIT_HEADER.search(text) is not None and _real_credential(text)


def file_has_git_credential(path: Path) -> bool:
    """The content rule for a tool that does not read the file itself (move): the whole file, streamed like _scan.
    A file that can't be read counts as no (the tool's own open then fails)."""
    header = cred = False
    tail = ""
    try:
        with path.open("rb") as fh:
            while block := fh.read(_CHUNK):
                text = tail + block.decode("utf-8", errors="replace")
                header = header or _GIT_HEADER.search(text) is not None
                cred = cred or _real_credential(text)
                if header and cred:
                    return True
                tail = text[-_OVERLAP:]
                tail = tail[tail.find("\n") + 1:]
    except OSError:
        return False
    return False


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
