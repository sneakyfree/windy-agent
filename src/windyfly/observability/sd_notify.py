"""systemd notify protocol — minimal client.

systemd's NOTIFY_SOCKET protocol lets a service tell its supervisor:

  - READY=1     — startup complete (paired with ``Type=notify``)
  - WATCHDOG=1  — heartbeat ping (paired with ``WatchdogSec=``); if
                  systemd doesn't see one within the configured window
                  it kills+restarts the service
  - STOPPING=1  — clean shutdown begun
  - STATUS=…    — human-readable line surfaced in ``systemctl status``

If ``NOTIFY_SOCKET`` is unset (running outside systemd — dev mode,
tests, ad-hoc invocation) every call is a silent no-op. No extra
dependency: uses the raw unix-datagram protocol systemd documents.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    """Send a state message to systemd's notify socket.

    Returns True if the datagram was delivered, False if no socket is
    set or the send failed. Failure is non-fatal — the service runs
    fine without watchdog supervision; this function only enables it.
    """
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return False
    # systemd uses an abstract socket when the path begins with "@".
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(sock_path)
            s.sendall(state.encode("utf-8"))
        return True
    except OSError as e:
        logger.debug("sd_notify(%r) failed: %s", state, e)
        return False


def notify_ready() -> bool:
    """Tell systemd the service is fully started. Pair with Type=notify."""
    return sd_notify("READY=1")


def notify_watchdog() -> bool:
    """Heartbeat ping — call once per heartbeat tick. Pair with WatchdogSec=.

    systemd kills+restarts the service if it doesn't see one of these
    within the configured window. Two contracts together: heartbeat
    interval << WatchdogSec.
    """
    return sd_notify("WATCHDOG=1")


def notify_stopping() -> bool:
    """Tell systemd we're starting graceful shutdown."""
    return sd_notify("STOPPING=1")
