"""Docker dispatcher for sandboxed capability execution.

Wave 5 #1 ships this for ``shell.exec``. Wave 5 #2's browser
capability will reuse the same dispatcher with different mount/cmd
shapes. The design decisions baked in here are all the ones Grant
blessed in PR #57's ``docs/wave5-shell-exec.md``:

  - **Container-per-call** (Decision 1) — ``docker run --rm``. State
    contained to the single invocation. Cold-start cost (~200ms once
    the image is cached) is the price for not having state leak
    between turns.
  - **Read-only mounts by default** (Decision 2) — ``allowed_roots``
    from the filesystem config get bind-mounted ``:ro`` so the agent
    can ``ls``/``cat``/``grep`` them via shell but can't modify
    without going through ``fs.write_file`` (which has its own audit
    + undo). OWNER band can opt into ``:rw`` per call.
  - **No command allowlist** (Decision 3) — Docker isolation is the
    real defense. The small ``BLOCKED_PATTERNS`` list in
    ``blocklist.py`` catches a few belt-and-suspenders cases.
  - **64KB output cap, 30s wall-clock default** (Decision 4) — bigger
    outputs come back truncated with size hints; longer commands
    timed-out cleanly via SIGTERM → 5s grace → SIGKILL.
  - **Exit code as data, not exception** (Decision 5) — non-zero
    exit returns a result envelope; only Docker-itself failures
    raise.
  - **Always-deny paths masked in the container** — every ``.ssh``,
    ``.aws``, ``.env``, etc. found under a mounted root (at any depth,
    by a bounded walk) gets an empty read-only layer over it; a root the
    walk can't finish is not mounted at all.

This file deliberately does NOT depend on the docker-py SDK. Calling
the ``docker`` CLI via subprocess is a smaller dependency surface,
gives precise stdout/stderr separation, and avoids the docker-py +
asyncio interaction footguns. Trade-off: we shell out to a
subprocess per call. For shell.exec that's fine (the user already
asked for shell-level latency).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import os

from windyfly.agent.capabilities import git_secrets
from windyfly.agent.capabilities.filesystem import _ALWAYS_DENY, deny_match

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "alpine:3.19"
DEFAULT_TIMEOUT_S = 30
DEFAULT_OUTPUT_CAP_BYTES = 64 * 1024
HARD_TIMEOUT_CEILING_S = 300  # 5-minute hard cap regardless of caller request
DEFAULT_MEMORY = "512m"

# Always-deny path tails, masked inside every mounted root (see build_mounts).
#
# Aliased from filesystem.py (imported at the top) rather than
# re-declared. This WAS a second hand-maintained copy — "same list as
# filesystem.py's _ALWAYS_DENY" — and a duplicated security list is a
# drift factory: PR #311's Windows separator fix landed on
# filesystem.py's matcher and never on this one, so .ssh stayed
# mountable here. One list, one place.
_ALWAYS_DENY_TAILS = _ALWAYS_DENY

# The mask walk's bounds. A root the walk can't finish inside them is NOT mounted (fail closed): set narrower
# [capabilities.filesystem] allowed_roots for a very large home.
_MASK_WALK_MAX_ENTRIES = 100_000
_MASK_WALK_MAX_DEPTH = 8
# Folders the walk doesn't enter (build output, dependency trees): never a home for the deny list's secrets.
_MASK_WALK_PRUNE = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"})


def _empty_mask_file() -> Path:
    """An empty read-only file to mount over a secret FILE. It lives in the state dir (~/.windy), which Docker Desktop
    shares on macOS; /dev/null is refused there ("path not shared"), Hub on #755."""
    from windyfly.platform import windy_state_dir

    path = windy_state_dir() / "sandbox" / "empty"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    path.chmod(0o444)
    return path


def deny_masks(src: Path, dst_base: str) -> list[str] | None:
    """Docker flags masking every always-deny path under ``src`` (mounted at /mnt/<dst_base>), at any depth.

    A folder gets an empty read-only tmpfs, a file an empty read-only file. A deny match that is a SYMLINK masks its
    resolved target when that target is under ``src`` (Hub on #755). The walk never enters .git, but every git config
    file that carries a credential is masked as a file (git_secrets: a repo's, a bare clone's, a submodule's, a linked
    worktree's). Returns None when the walk exceeds its bounds: the caller then does not mount ``src`` at all.
    """
    flags: list[str] = []
    masked: set[str] = set()
    seen = 0
    root = str(src).replace("\\", "/").rstrip("/")

    def mask(target: Path, *, as_dir: bool | None = None) -> None:
        rel = target.relative_to(src).as_posix()
        dst = f"/mnt/{dst_base}/{rel}"
        if dst in masked:
            return               # Docker refuses two mounts on one path
        masked.add(dst)
        if target.is_dir() if as_dir is None else as_dir:
            flags.extend(["--tmpfs", f"{dst}:ro,nosuid,nodev,noexec,size=1k"])
        else:
            flags.extend(["-v", f"{_empty_mask_file()}:{dst}:ro"])

    def check(path: Path) -> bool:
        if deny_match(str(path).replace("\\", "/")) is None:
            return False
        if path.is_symlink():
            real = path.resolve(strict=False)
            if real.exists() and real.is_relative_to(src):
                mask(real)       # the secret is inside this root under another name: hide it there
            # And the link path itself, never followed out of the root (Hub on #755).
            mask(path, as_dir=real.is_dir())
            return True
        mask(path)
        return True

    def inside(p: Path) -> Path | None:
        real = p.resolve(strict=False)
        return real if real.exists() and real.is_relative_to(src) else None

    def git_config(cfg: Path) -> None:
        """A git config file: masked when it carries a credential. A symlinked one is masked as a link (never followed
        out of the root), and its target too when that is under this root and carries one (Hub on #761)."""
        if cfg.is_symlink():
            mask(cfg, as_dir=False)
            real = inside(cfg)
            if real is not None and real.is_file() and git_secrets.has_credential(real):
                mask(real, as_dir=False)
        elif cfg.is_file() and git_secrets.has_credential(cfg):
            mask(cfg, as_dir=False)
        # The files this config includes (one level), when under this root: a token can sit there under any name.
        own = inside(cfg) if cfg.is_symlink() else cfg
        if own is not None and own.is_file():
            for inc in git_secrets.include_targets(own):
                if inc.is_relative_to(src) and inc.is_file() and git_secrets.has_credential(inc):
                    mask(inc, as_dir=False)

    def git_dir(gdir: Path, depth: int = 0) -> None:
        """A .git folder the walk does not enter: check its config files here. A symlinked submodule or worktree
        folder is masked as a link, and its target checked when it is under this root."""
        files, links = git_secrets.git_dir_entries(gdir)
        for cfg in files:
            git_config(cfg)
        for link in links:
            mask(link, as_dir=True)
            real = inside(link)
            if real is not None and real.is_dir() and depth < 2:
                if link.parent.name in ("modules", "worktrees"):
                    for n in sorted(git_secrets.GIT_CONFIG_NAMES):
                        if (real / n).is_file() or (real / n).is_symlink():
                            git_config(real / n)
                else:
                    git_dir(real, depth + 1)

    for dirpath, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
        here = Path(dirpath)
        depth = len(here.relative_to(src).parts)
        seen += len(dirnames) + len(filenames)
        if seen > _MASK_WALK_MAX_ENTRIES or depth > _MASK_WALK_MAX_DEPTH:
            logger.warning("shell sandbox: root %s too large to check for secret folders; not mounted", root)
            return None
        keep = []
        for d in dirnames:
            if d in _MASK_WALK_PRUNE:
                if d == ".git":          # a credentialed remote in a repo's config (Hub on #755 / #761)
                    if not (here / d).is_symlink():
                        git_dir(here / d)
                    elif (real := inside(here / d)) is not None and real.is_dir():
                        git_dir(real)
                continue
            if check(here / d):
                continue         # masked whole: no need to look inside
            if not (here / d).is_symlink():
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            if not check(here / f) and git_secrets.is_git_config(here / f):
                git_config(here / f)     # a bare or mirror clone (name.git/config), walked like any folder
    return flags


class DockerNotAvailable(RuntimeError):
    """Raised when ``docker`` isn't on PATH or the daemon isn't reachable."""


@dataclass
class DockerExecResult:
    """Envelope returned by DockerDispatcher.run()."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    timed_out: bool
    sandbox_tier: str
    image: str
    network: str
    mounts: list[str]


class DockerDispatcher:
    """Wraps the ``docker`` CLI for safe single-command execution."""

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        memory: str = DEFAULT_MEMORY,
        docker_bin: str | None = None,
    ) -> None:
        self.image = image
        self.memory = memory
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

    def is_available(self) -> bool:
        """Cheap check: is docker on PATH and the daemon reachable."""
        try:
            r = subprocess.run(
                [self.docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def build_mounts(
        self,
        allowed_roots: list[str],
        *,
        read_write: bool = False,
    ) -> list[str]:
        """Translate ``allowed_roots`` into ``-v src:dst:mode`` flags.

        Each allowed root mounts at ``/mnt/<basename>``. Always-deny
        paths inside the root aren't excluded individually here —
        instead, the *containing root* gets the mount and the
        always-deny tails are skipped at the per-mount-source level.
        For our purposes this means: if the root is ``~/`` and
        ``~/.ssh`` exists, the mount creates ``/mnt/Home`` containing
        everything under ``~/`` *except* `.ssh` won't be reachable
        via filesystem capabilities (because ``_resolve_and_check``
        from Wave 3 #1 still gates them). Inside the container the
        path *will* exist, but the agent can't reach it via any
        first-class capability.

        Inside the container every always-deny path under a mounted root is MASKED (Boss's hardening list, item
        "shell.exec roots"): a folder gets an empty read-only tmpfs over it, a file gets /dev/null over it. Before,
        the container could ``ls /mnt/Home/.ssh`` and read it, and with network=true send it out.
        """
        mode = "rw" if read_write else "ro"
        flags: list[str] = []
        seen_basenames: set[str] = set()
        for root in allowed_roots:
            src = Path(root).expanduser().resolve(strict=False)
            if not src.exists():
                continue
            # Skip the entire root if it's itself an always-deny tail.
            #
            # Separators are normalized to '/' first. On Windows
            # `str(Path.resolve())` is backslash-delimited, so the old
            # `str(src).rsplit("/", 1)[-1]` returned the WHOLE path and
            # the `endswith("/" + d)` test never matched — meaning
            # ~/.ssh was bind-mounted into the sandbox at /mnt/.ssh.
            #
            # This is the same defect PR #311 fixed in filesystem.py
            # ("capability deny-list must match on Windows separators
            # too"). It survived here because this module kept its OWN
            # copy of the list and its own matcher, so the fix landed on
            # one of the two and the copy silently drifted. Caught on the
            # GrantW Windows 11 box, where the test expecting [] got
            # ['-v', 'C:\\Users\\...\\.ssh:/mnt/.ssh:ro'].
            #
            # Matches a trailing component OR any interior component, so
            # multi-part entries like '.docker/config.json' work too.
            src_str = str(src).replace("\\", "/").rstrip("/")
            if deny_match(src_str) is not None:
                continue
            base = src.name or "root"
            # Resolve basename collisions deterministically
            dst_base = base
            counter = 1
            while dst_base in seen_basenames:
                counter += 1
                dst_base = f"{base}_{counter}"
            seen_basenames.add(dst_base)
            masks = deny_masks(src, dst_base)
            if masks is None:
                seen_basenames.discard(dst_base)
                continue         # too large to check: not mounted (fail closed)
            flags.extend(["-v", f"{src}:/mnt/{dst_base}:{mode}"])
            flags.extend(masks)
        return flags

    def run(
        self,
        command: str,
        *,
        allowed_roots: list[str] | None = None,
        read_write: bool = False,
        network: bool = False,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES,
    ) -> DockerExecResult:
        """Run ``command`` inside a fresh ``--rm`` container.

        Returns a DockerExecResult with the captured output. Non-zero
        exit codes are *data* in the envelope, not exceptions —
        callers (the LLM) treat them as "the command failed" rather
        than "the agent broke." Real exceptions (Docker daemon down,
        image pull failure) raise DockerNotAvailable.
        """
        if not self.is_available():
            raise DockerNotAvailable(
                "docker CLI not on PATH or daemon not reachable"
            )

        # Cap timeout at the hard ceiling regardless of caller request
        effective_timeout = min(max(1, timeout_s), HARD_TIMEOUT_CEILING_S)

        mounts = self.build_mounts(allowed_roots or [], read_write=read_write)
        net_flags = [] if network else ["--network=none"]

        cmd = [
            self.docker_bin, "run",
            "--rm",
            f"--memory={self.memory}",
            "--pids-limit=512",
            *net_flags,
            *mounts,
            self.image,
            "/bin/sh", "-c", command,
        ]

        logger.info(
            "docker run image=%s network=%s mounts=%d timeout=%ds",
            self.image,
            "host" if network else "none",
            len(mounts) // 2,
            effective_timeout,
        )

        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=effective_timeout,
            )
            timed_out = False
            exit_code = proc.returncode
            stdout_b = proc.stdout
            stderr_b = proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = -1
            stdout_b = e.stdout or b""
            stderr_b = e.stderr or b""
        except FileNotFoundError as exc:
            raise DockerNotAvailable(f"docker binary missing: {exc}") from exc

        duration_ms = int((time.time() - started) * 1000)

        stdout, stdout_truncated = _truncate(stdout_b, output_cap_bytes)
        stderr, stderr_truncated = _truncate(stderr_b, output_cap_bytes)

        return DockerExecResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_ms=duration_ms,
            timed_out=timed_out,
            sandbox_tier="docker",
            image=self.image,
            network="host" if network else "none",
            mounts=mounts,
        )


def _truncate(data: bytes, cap: int) -> tuple[str, bool]:
    """Decode UTF-8 best-effort, capping at ``cap`` bytes."""
    if len(data) > cap:
        return data[:cap].decode("utf-8", errors="replace"), True
    return data.decode("utf-8", errors="replace"), False
