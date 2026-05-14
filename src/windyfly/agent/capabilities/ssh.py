"""ssh.exec capability — Tier 0 Stock Toolkit, PR closing the gap
Grant surfaced 2026-05-12 when Windy 0 told him "I don't have an SSH
tool" in response to "SSH over to Veron-1 and check if it's up."

Pattern matches ``shell.exec`` (Wave 5 #1) — same risk class, same
output shape, same band-gated discipline. The difference is the
sandbox model: SSH targets a remote host the user already configured
in ``~/.ssh/config``, so the trust boundary is the user's own ssh
setup rather than Docker. We do not reinvent the SSH client; we
shell out to the system ``ssh`` binary so all the user's existing
keys, config, and known_hosts Just Work.

Security posture:

  - Default ``band_required = TRUSTED``. SSH is "do things on a
    remote machine" — same risk class as shell on the local box.
  - Hosts listed in ``WINDY_SSH_ALLOWED_HOSTS`` (env, comma-separated)
    are pre-authorized by the instance owner. For those, USER band
    is enough — a verified user can SSH to the fleet without
    elevating to TRUSTED. Unknown hosts stay at TRUSTED.
  - ``localhost`` / ``127.*`` is rejected. Use ``shell.exec`` instead;
    SSH-to-loopback is a sandbox bypass attempt or a footgun.
  - ``StrictHostKeyChecking=accept-new`` — first-time hosts are
    accepted (and recorded in known_hosts), but a changed host key
    still fails closed (MITM defense).
  - ``ConnectTimeout=10`` so dead hosts fail fast.
  - The same blocklist used by ``shell.exec`` (rm -rf /, fork bomb,
    mkfs, etc.) screens the remote command pre-flight. Belt and
    suspenders — the LLM still has to *want* to run something
    catastrophic for the attempt to even reach SSH.
  - Output capped at 64KB (matches ``shell.exec``); 5-minute hard
    timeout ceiling.

The result dict mirrors ``shell.exec`` so any downstream consumer
(audit logs, episode storage, retry logic) can treat ssh and shell
outputs interchangeably.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any

from windyfly.agent.capabilities.descriptor import (
    Capability,
    Reversibility,
    SandboxTier,
    Tier,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.sandbox import (
    BlockedCommand,
    check_blocklist,
)

logger = logging.getLogger(__name__)


# Reject SSH to anything that resolves to the local host — the
# correct tool there is shell.exec. SSH-to-loopback bypasses the
# Docker sandbox shell.exec uses by default; this guard closes
# that escape hatch.
_LOOPBACK_PATTERNS = (
    "localhost",
    "127.",
    "::1",
    "0.0.0.0",
)

# Tight bound on the timeout so a hung ssh process can't camp the
# capability dispatcher forever. Mirrors shell.exec's 5-minute cap.
_HARD_TIMEOUT_CEILING_S = 300
_DEFAULT_TIMEOUT_S = 30

# Output cap — matches shell.exec.
_OUTPUT_CAP_BYTES = 64 * 1024


def _allowed_hosts() -> set[str]:
    """Comma-separated list of pre-authorized hosts (env-driven).

    Set ``WINDY_SSH_ALLOWED_HOSTS`` in the instance's soul-repo .env
    (per the 2026-04-21 architectural rule: instance config lives in
    the soul repo, not in this codebase). Entries can be:

      - bare host aliases that resolve via ~/.ssh/config: ``wg-0c2``
      - explicit user@host pairs: ``oc1-gpu@10.10.0.6``
      - bare IPs: ``10.10.0.6``

    When the requested host matches an entry, the runtime tier
    check downgrades the band requirement from TRUSTED to USER —
    the verified user can reach the fleet without elevating.
    """
    raw = os.environ.get("WINDY_SSH_ALLOWED_HOSTS", "")
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


def _host_is_loopback(host: str) -> bool:
    h = host.lower()
    # Strip user@ prefix if present
    if "@" in h:
        h = h.split("@", 1)[1]
    return any(h == p.rstrip(".") or h.startswith(p) for p in _LOOPBACK_PATTERNS)


def _host_is_allowed(host: str) -> bool:
    allowed = _allowed_hosts()
    if not allowed:
        return False
    h = host.strip()
    # Exact match, with-user match, or stripped-user match all count
    if h in allowed:
        return True
    if "@" in h and h.split("@", 1)[1] in allowed:
        return True
    return False


def _ssh_runtime_tier_check(args: dict[str, Any]) -> Tier | None:
    """Bump tier (and therefore required band) for non-whitelisted hosts.

    Defaults to ``EXTERNAL_EFFECT`` (TRUSTED). For unknown hosts we
    escalate to ``FULL_MACHINE`` (OWNER) — the operator should be in
    the loop when a new outbound SSH lands.
    """
    host = (args or {}).get("host", "")
    if not host:
        return None
    if _host_is_allowed(host):
        # Pre-authorized: stay at the static tier (TRUSTED). Returning
        # None means "no override".
        return None
    # Unknown host: bump to FULL_MACHINE so OWNER is required.
    return Tier.FULL_MACHINE


def _ssh_exec_handler(
    *,
    host: str,
    command: str,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute ``command`` on ``host`` via the system ``ssh`` binary.

    Returns a dict mirroring ``shell.exec``'s shape:

        {
          "host", "command", "exit_code",
          "stdout", "stderr",
          "stdout_truncated", "stderr_truncated",
          "duration_ms", "timed_out",
          "sandbox_tier", "outcome_score",
        }

    Non-zero exit is data, not an exception — only SSH-itself
    failures (e.g., host unreachable, key rejected) raise.
    """
    if not host or not host.strip():
        raise ValueError("ssh.exec requires a non-empty host")
    if not command or not command.strip():
        raise ValueError("ssh.exec requires a non-empty command")
    if _host_is_loopback(host):
        raise PermissionError(
            f"ssh.exec refused: {host!r} is loopback. Use shell.exec "
            "for local commands — it sandboxes via Docker by default."
        )

    # Pre-flight blocklist (same screen as shell.exec). A user who
    # wants to legitimately run a dangerous command on a remote box
    # can still escalate via OWNER band + future force-confirm; the
    # blocklist is the basic foot-gun guard.
    try:
        check_blocklist(command)
    except BlockedCommand as e:
        raise PermissionError(f"ssh.exec command blocked: {e}") from e

    timeout_s = max(1, min(int(timeout_s), _HARD_TIMEOUT_CEILING_S))

    argv = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",  # no interactive password prompts
        host,
        command,
    ]

    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        stdout_b, stderr_b = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout_b = exc.stdout or b""
        stderr_b = exc.stderr or b""
    except FileNotFoundError as exc:
        # No ssh binary on PATH — surface a clear error rather than
        # the cryptic FileNotFoundError.
        raise RuntimeError(
            "ssh.exec requires the system 'ssh' binary on PATH. "
            "Install openssh-client (apt/dnf) or ensure /usr/bin/ssh "
            "is reachable."
        ) from exc

    duration_ms = int((time.time() - started) * 1000)
    stdout = stdout_b[:_OUTPUT_CAP_BYTES].decode("utf-8", errors="replace")
    stderr = stderr_b[:_OUTPUT_CAP_BYTES].decode("utf-8", errors="replace")

    return {
        "host": host,
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": len(stdout_b) > _OUTPUT_CAP_BYTES,
        "stderr_truncated": len(stderr_b) > _OUTPUT_CAP_BYTES,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "sandbox_tier": SandboxTier.REMOTE,
        "outcome_score": 1.0 if not timed_out and exit_code == 0 else 0.0,
    }


def register_ssh_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register ``ssh.exec`` on the capability registry.

    No config knobs in v1 — hosts are env-driven via
    ``WINDY_SSH_ALLOWED_HOSTS`` so the soul repo's .env owns the
    instance-specific list.
    """
    allowed = sorted(_allowed_hosts())
    logger.info(
        "Registering ssh.exec — pre-authorized hosts: %s",
        allowed or "(none — all SSH requires OWNER band)",
    )

    def ssh_exec(
        *, host: str,
        command: str,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        return _ssh_exec_handler(
            host=host, command=command, timeout_s=timeout_s,
        )

    registry.register(Capability(
        id="ssh.exec",
        description=(
            "Run a command on a remote host via SSH. Uses the system "
            "'ssh' binary, so your ~/.ssh/config aliases and existing "
            "keys Just Work. Default 30s timeout, 5-minute ceiling, "
            "64KB output cap. Loopback (localhost/127.*) is rejected — "
            "use shell.exec for local. Pre-authorized hosts (set via "
            "WINDY_SSH_ALLOWED_HOSTS env, comma-separated) run at USER "
            "band; unknown hosts require OWNER band. Output truncated "
            "at 64KB; the same blocklist as shell.exec screens "
            "obviously-catastrophic commands pre-flight."
        ),
        handler=ssh_exec,
        input_schema={
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": (
                        "Target host. Can be a ~/.ssh/config alias "
                        "(e.g., 'wg-0c2', 'vps'), a user@host pair, "
                        "or a bare IP. Loopback addresses are "
                        "rejected — use shell.exec for local commands."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Command to run on the remote host. Same "
                        "pre-flight blocklist as shell.exec applies "
                        "(rm -rf /, fork bomb, mkfs, etc. blocked)."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": (
                        "Wall-clock seconds before the SSH process is "
                        "killed (default 30, 5-minute hard ceiling)."
                    ),
                },
            },
            "required": ["host", "command"],
        },
        tier=Tier.EXTERNAL_EFFECT,  # Default — runtime check may escalate
        runtime_tier_check=_ssh_runtime_tier_check,
        sandbox_tier=SandboxTier.REMOTE,
        reversibility=Reversibility.EXTERNAL_EFFECT,
        scope="remote_shell",
    ))
