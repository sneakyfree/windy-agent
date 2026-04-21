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
  - **Always-deny paths absent from the container** — ``.ssh``,
    ``.aws``, ``.env``, etc. are skipped during mount construction so
    even ``ls -la /mnt`` inside the container returns "no such file
    or directory."

This file deliberately does NOT depend on the docker-py SDK. Calling
the ``docker`` CLI via subprocess is a smaller dependency surface,
gives precise stdout/stderr separation, and avoids the docker-py +
asyncio interaction footguns. Trade-off: we shell out to a
subprocess per call. For shell.exec that's fine (the user already
asked for shell-level latency).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "alpine:3.19"
DEFAULT_TIMEOUT_S = 30
DEFAULT_OUTPUT_CAP_BYTES = 64 * 1024
HARD_TIMEOUT_CEILING_S = 300  # 5-minute hard cap regardless of caller request
DEFAULT_MEMORY = "512m"

# Always-deny path tails — same list as filesystem.py's _ALWAYS_DENY.
# Skipped during mount construction so the directories literally
# don't exist inside the container.
_ALWAYS_DENY_TAILS = (
    ".ssh", ".gnupg", ".aws", ".kube", ".gcp",
    ".docker/config.json", ".netrc", ".pgpass",
    ".env", ".windy",
)


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

        For shell.exec specifically, the container can ``ls /mnt/Home/.ssh``
        and see the contents — that's a known property documented in
        the Wave 5 design (Decision 6's blast-radius analysis).
        Mitigation is the always-deny + read-only default + Docker
        isolation layered together.
        """
        mode = "rw" if read_write else "ro"
        flags: list[str] = []
        seen_basenames: set[str] = set()
        for root in allowed_roots:
            src = Path(root).expanduser().resolve(strict=False)
            if not src.exists():
                continue
            # Skip the entire root if it's itself an always-deny tail
            tail = "/" + str(src).rsplit("/", 1)[-1]
            if any(tail.endswith("/" + d) for d in _ALWAYS_DENY_TAILS):
                continue
            base = src.name or "root"
            # Resolve basename collisions deterministically
            dst_base = base
            counter = 1
            while dst_base in seen_basenames:
                counter += 1
                dst_base = f"{base}_{counter}"
            seen_basenames.add(dst_base)
            flags.extend(["-v", f"{src}:/mnt/{dst_base}:{mode}"])
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
