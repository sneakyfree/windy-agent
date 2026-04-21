"""Pre-flight blocklist for shell commands.

The Wave 5 design (Decision 3) said: rely on Docker isolation as the
real defense, with a small blocklist as belt-and-suspenders for the
patterns that are still bad inside the container (fork bomb starves
host CPU briefly; ``curl | sh`` running random malware that
exfiltrates allowed_roots even via read-only is theoretically
possible).

This is intentionally short. Adding patterns is cheap; getting the
list right matters more than coverage. If a pattern catches false
positives in real use, we shrink rather than refusing user requests.
"""

from __future__ import annotations

import re

# Patterns we refuse pre-Docker. Each entry is (regex, reason) so the
# error message tells the LLM what to do differently.
BLOCKED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        # The classic fork bomb. Fits on one line; not catchable by
        # token allowlists; would burn host CPU even with --cpus
        # caps because the daemon scheduling overhead spikes.
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "fork bomb pattern blocked",
    ),
    (
        # rm -rf / and rm -rf /* — even inside Docker with read-only
        # mounts these waste container time and surface confusing
        # errors. With host_rw mounts on OWNER bypass, they're
        # actually catastrophic.
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--recursive\s+--force|--force\s+--recursive)[a-zA-Z]*\s+/(\s|$|\*)"),
        "rm -rf / pattern blocked — too dangerous regardless of sandbox",
    ),
    (
        # `curl <url> | sh` and friends — pipe-into-shell is the
        # canonical malware delivery shape. Block both directions.
        re.compile(r"\b(curl|wget|fetch)\s+[^|;&]+\|\s*(bash|sh|zsh|ksh|fish)\b"),
        "pipe-from-network-to-shell pattern blocked (use download-then-inspect)",
    ),
    (
        # `dd if= ... of=/dev/sd*` — overwriting block devices.
        re.compile(r"\bdd\s+[^;]*of=/dev/(sd|hd|nvme|disk|xvd)"),
        "dd to block device blocked",
    ),
    (
        # `mkfs` / `mkfs.*` — formatting filesystems. Even with
        # --network=none mounted under /mnt this can clobber
        # bind-mounted host content if user opted into host_rw.
        re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"),
        "mkfs blocked",
    ),
)


class BlockedCommand(Exception):
    """Raised when a command matches the blocklist before Docker runs."""


def check_blocklist(command: str) -> None:
    """Raise BlockedCommand if ``command`` matches any blocked pattern.

    The exception message includes the matched-pattern reason so the
    typed-error classifier (#50) can hand the LLM something actionable
    ('rm -rf / pattern blocked') instead of a generic refusal.
    """
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(command):
            raise BlockedCommand(reason)
