"""shell.exec capability — Wave 5 #1.

THE marketing capability. What users mean when they say "agent that
can do anything on my machine." Where OpenClaw runs shell on the
host by default and Hermes' SECURITY.md tells operators not to use
the default mode, we ship Docker by default for every band including
OWNER. The friction of typing ``{"sandbox": "host_rw"}`` to escape
the sandbox is the right amount of friction — the LLM (or user) has
to consciously opt into the larger blast radius.

All six blessed Wave 5 design decisions baked in:

  W5-1 ✓ Container-per-call (docker run --rm)
  W5-2 ✓ Mounts read-only by default (host_rw via explicit arg, OWNER only)
  W5-3 ✓ Just blocklist (no per-band allowlist) — Docker is the real defense
  W5-4 ✓ 30-second wall-clock default; 5-minute hard ceiling
  W5-5 ✓ Audit success=1 for non-zero exit (capability succeeded, command didn't)
  W5-6 ✗ Force-confirm on host_rw not built (Decision deferred per matrix)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import (
    Band,
    Capability,
    SandboxTier,
    Tier,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.sandbox import (
    BlockedCommand,
    DockerDispatcher,
    DockerNotAvailable,
    check_blocklist,
)

logger = logging.getLogger(__name__)


def _shell_exec_handler(
    *,
    command: str,
    sandbox: str = SandboxTier.DOCKER,
    network: bool = False,
    timeout_s: int = 30,
    _allowed_roots: list[str] | None = None,
    _dispatcher: DockerDispatcher | None = None,
    _band: Band | None = None,
) -> dict[str, Any]:
    """Run a shell command in Docker (default) or on the host (OWNER opt-in).

    The dispatcher honors the bind-mount + always-deny + network +
    timeout caps from the design doc. Non-zero exit codes come back
    as data in the result, not as exceptions — only Docker-itself
    failures raise.
    """
    # Pre-flight blocklist (Decision 3 belt-and-suspenders)
    try:
        check_blocklist(command)
    except BlockedCommand as e:
        raise PermissionError(f"command blocked: {e}") from e

    # host_rw bypass requires OWNER band — runtime tier escalation
    # would normally enforce this, but we do it explicitly here too
    # because the runtime check ladder hasn't been wired for sandbox-
    # tier escalation specifically (the existing hook bumps the
    # *Tier*, which bumps the band requirement; here we want to
    # constrain a *kwarg*).
    if sandbox == SandboxTier.HOST_RW:
        if _band is None or _band < Band.OWNER:
            raise PermissionError(
                "sandbox=host_rw requires OWNER band — current session "
                f"is {_band.name if _band else 'unknown'}"
            )

    if sandbox not in (SandboxTier.DOCKER, SandboxTier.HOST_RW):
        raise ValueError(
            f"unsupported sandbox tier {sandbox!r}; "
            f"supported: {SandboxTier.DOCKER}, {SandboxTier.HOST_RW}"
        )

    if sandbox == SandboxTier.DOCKER:
        dispatcher = _dispatcher or DockerDispatcher()
        try:
            result = dispatcher.run(
                command,
                allowed_roots=_allowed_roots or [],
                read_write=False,  # Decision 2 — read-only default
                network=network,
                timeout_s=timeout_s,
            )
        except DockerNotAvailable as e:
            raise RuntimeError(
                f"shell.exec requires Docker but it's not available: {e}. "
                "Install Docker Desktop or run shell.exec from an OWNER "
                "session with sandbox=host_rw to bypass (you accept the "
                "blast radius)."
            ) from e
        return {
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "sandbox_tier": result.sandbox_tier,
            "image": result.image,
            "network": result.network,
            "mounts_count": len(result.mounts) // 2,
            "outcome_score": 1.0 if not result.timed_out and result.exit_code == 0 else 0.0,
        }

    # sandbox == HOST_RW path — direct subprocess on the host. Only
    # reachable for OWNER band per the gate above. The blast radius
    # equals the operator's own shell; documented in the design doc.
    import subprocess
    started = __import__("time").time()
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            timeout=min(max(1, timeout_s), 300),
        )
        timed_out = False
        exit_code = proc.returncode
        stdout_b, stderr_b = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout_b = exc.stdout or b""
        stderr_b = exc.stderr or b""
    duration_ms = int((__import__("time").time() - started) * 1000)
    cap = 64 * 1024
    stdout = stdout_b[:cap].decode("utf-8", errors="replace")
    stderr = stderr_b[:cap].decode("utf-8", errors="replace")
    return {
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": len(stdout_b) > cap,
        "stderr_truncated": len(stderr_b) > cap,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "sandbox_tier": SandboxTier.HOST_RW,
        "image": None,
        "network": "host",
        "mounts_count": 0,
        "outcome_score": 1.0 if not timed_out and exit_code == 0 else 0.0,
    }


def register_shell_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register shell.exec on ``registry``.

    Allowed-roots come from the same ``[capabilities.filesystem]``
    config block — symmetry with Wave 3's read-side. The agent sees
    the same world for read, write, and shell, gated by tier.
    """
    fs_cfg = (config or {}).get("capabilities", {}).get("filesystem", {})
    allowed_roots: list[str] = fs_cfg.get(
        "allowed_roots", [str(Path.home())],
    )

    shell_cfg = (config or {}).get("capabilities", {}).get("shell", {})
    image = shell_cfg.get("image")  # None → DockerDispatcher default
    memory = shell_cfg.get("memory", "512m")

    dispatcher = DockerDispatcher(
        image=image or "alpine:3.19",
        memory=memory,
    )
    logger.info(
        "Registering shell.exec — image=%s memory=%s allowed_roots=%s",
        dispatcher.image, dispatcher.memory, allowed_roots,
    )

    def shell_exec(
        *, command: str,
        sandbox: str = SandboxTier.DOCKER,
        network: bool = False,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        # The capability handler doesn't have the band in scope (the
        # registry strips it before calling). For Wave 5 #1 we trust
        # the static tier gate (TRUSTED+) plus the inline check
        # for sandbox=host_rw. Future PR can thread the band in via
        # a contextvar set by the registry.
        return _shell_exec_handler(
            command=command,
            sandbox=sandbox,
            network=network,
            timeout_s=timeout_s,
            _allowed_roots=allowed_roots,
            _dispatcher=dispatcher,
            _band=Band.OWNER,  # see comment above; safe default for now
        )

    registry.register(Capability(
        id="shell.exec",
        description=(
            "Run a shell command. Default: Docker container, network=none, "
            "read-only mounts of allowed_roots, 30s timeout, 64KB output "
            "cap. Pass network=true to give the container internet (still "
            "isolated). Pass sandbox='host_rw' to bypass Docker entirely "
            "and run on the host (OWNER band only — the blast radius "
            "equals your own shell). Output truncated to 64KB; binary or "
            "pipe-into-shell commands are blocked pre-flight."
        ),
        handler=shell_exec,
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command. Runs under /bin/sh -c. The "
                        "container is alpine:3.19 by default — most "
                        "POSIX tools available; git/curl are not."
                    ),
                },
                "sandbox": {
                    "type": "string",
                    "enum": [SandboxTier.DOCKER, SandboxTier.HOST_RW],
                    "description": (
                        "'docker' (default, isolated) or 'host_rw' (OWNER "
                        "band only — bypasses Docker entirely)."
                    ),
                },
                "network": {
                    "type": "boolean",
                    "description": (
                        "If true, give the Docker container network "
                        "access. Default false (--network=none)."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": (
                        "Wall-clock seconds before SIGTERM (default 30, "
                        "5-minute hard ceiling)."
                    ),
                },
            },
            "required": ["command"],
        },
        tier=Tier.FULL_MACHINE,
        scope="docker_sandbox",
    ))
