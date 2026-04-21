"""Sandbox dispatch — where capabilities actually run.

Currently ships the Docker dispatcher (Wave 5 #1). Future tiers:
``host_rw`` for OWNER-band trusted operations, ``remote`` for
Modal/Daytona/Singularity backends (Wave 5 #3).

The Capability descriptor's ``sandbox_tier`` field declares where a
capability *should* run; the dispatcher in this module is what
actually puts it there. Separating dispatch from capability handlers
means Wave 5 #2's browser capability (which also wants Docker
isolation) reuses the same DockerDispatcher without re-litigating
how mounts work.
"""

from windyfly.agent.capabilities.sandbox.blocklist import (
    BLOCKED_PATTERNS,
    BlockedCommand,
    check_blocklist,
)
from windyfly.agent.capabilities.sandbox.docker import (
    DockerDispatcher,
    DockerExecResult,
    DockerNotAvailable,
)

__all__ = [
    "BLOCKED_PATTERNS",
    "BlockedCommand",
    "DockerDispatcher",
    "DockerExecResult",
    "DockerNotAvailable",
    "check_blocklist",
]
