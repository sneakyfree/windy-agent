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
#
# All regexes are case-insensitive (re.IGNORECASE). Audit
# 2026-05-02 surfaced 'rm -RF /' (caps) and 'DD if=x of=/dev/sda'
# (caps) bypassing the original case-sensitive patterns; tightened.
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
        #
        # Audit 2026-05-02 tightening:
        #   - Flag-order independence: 'rm -fr /' previously bypassed
        #     because the regex required r-before-f. Two lookaheads
        #     now require both letters present in any order.
        #   - Quoted-path termination: 'bash -c "rm -rf /"' and
        #     'bash -c \'rm -rf /\'' previously bypassed because the
        #     trailing terminator required whitespace/EOS/*. Now also
        #     accepts a closing quote.
        #   - Optional quote BEFORE the / handles 'rm -rf "/"'.
        #   - Case-insensitive (rm -RF / now blocks).
        re.compile(
            r"\brm\s+"
            r"(-(?=[a-zA-Z]*r)(?=[a-zA-Z]*f)[a-zA-Z]+"
            r"|--recursive\s+--force"
            r"|--force\s+--recursive)"
            r"[a-zA-Z]*\s+"
            r"['\"]?/"
            r"(\s|$|\*|['\"])",
            re.IGNORECASE,
        ),
        "rm -rf / pattern blocked — too dangerous regardless of sandbox",
    ),
    (
        # `curl <url> | sh` and friends — pipe-into-shell is the
        # canonical malware delivery shape. Block both directions.
        re.compile(
            r"\b(curl|wget|fetch)\s+[^|;&]+\|\s*(bash|sh|zsh|ksh|fish)\b",
            re.IGNORECASE,
        ),
        "pipe-from-network-to-shell pattern blocked (use download-then-inspect)",
    ),
    (
        # `dd if= ... of=/dev/sd*` — overwriting block devices.
        # Audit 2026-05-02: now also catches quoted paths
        # ('of="/dev/sda"') and uppercase ('DD').
        re.compile(
            r"\bdd\s+[^;]*of=['\"]?/dev/(sd|hd|nvme|disk|xvd)",
            re.IGNORECASE,
        ),
        "dd to block device blocked",
    ),
    (
        # `mkfs` / `mkfs.*` — formatting filesystems. Even with
        # --network=none mounted under /mnt this can clobber
        # bind-mounted host content if user opted into host_rw.
        re.compile(r"\bmkfs(\.[a-z0-9]+)?\b", re.IGNORECASE),
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
    if names_the_lockbox(command):
        raise BlockedCommand(LOCKBOX_REASON)


# The owner's lockbox, by name, at EVERY band and in every sandbox, the owner included (Boss, BOARD
# 2026-11-13T01:38:19Z: "if Zero has any shell or exec tool, the same matcher goes there"). The same name matcher
# as the file and github tools (filesystem.is_lockbox_name). A text match is a FLOOR: a glob, a variable or a
# built-up string gets past it. The Docker sandbox also masks the lockbox (deny_masks -> deny_match); host_rw has
# only this floor.
LOCKBOX_REASON = ("command names the owner's credentials store (the lockbox): this agent never opens it, even for "
                  "the owner; credentials are fetched by name by the people and tools that hold them")
_WORD_SPLIT = re.compile(r"""[\s/\\'"`=:;|&()<>$,{}\[\]]+""")


def names_the_lockbox(command: str) -> bool:
    from windyfly.agent.capabilities.filesystem import is_lockbox_name

    return any(is_lockbox_name(part) for part in _WORD_SPLIT.split(command or "") if part)
