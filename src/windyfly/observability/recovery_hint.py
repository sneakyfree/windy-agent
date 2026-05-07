"""Centralized recovery-hint footer for error replies.

Pre-PR every error path in the bot wrote its own message. Some
mentioned /reset; some mentioned /resurrect; some mentioned both;
most mentioned neither. A confused grandma reading "⚠ Couldn't read
the cost ledger right now." had no idea what to do next.

This module exposes a single helper:

    with_recovery_hint(msg) → msg + "_If I'm still acting up — try
    /reset, or /resurrect to switch to a free local model._"

Call it at every error ack site. The footer is consistent across
every surface (telegram, matrix, future channels) and grandma can
LEARN it once.

Idempotent guard: if the message already mentions /reset OR
/resurrect, the helper returns it unchanged. Prevents the
embarrassing double-reference where a pause-flag-write-failure
already says "use /reset instead" and we'd append "...try /reset, or
/resurrect..." on top.

Why a separate module from sanitize.py: sanitize REMOVES harmful
content (tracebacks, secrets, control chars). This ADDS helpful
content. Different concerns, different shape, easy to test in
isolation.
"""

from __future__ import annotations


# The exact footer text. One italic line; Telegram parse_mode
# 'Markdown' renders the underscores as italics so the hint visually
# separates from the error body without taking up vertical space.
RECOVERY_HINT = (
    "_If I'm still acting up — try /reset, or /resurrect to switch "
    "to a free local model._"
)


def with_recovery_hint(msg: str | None) -> str:
    """Append the standard recovery hint to an error message, unless
    the message already mentions /reset or /resurrect.

    Returns the message unchanged when:
      - input is empty / None (nothing to attach to)
      - message already mentions /reset (e.g., "use /reset instead")
      - message already mentions /resurrect

    Otherwise appends two newlines + the italic hint footer.

    Example::

        ack = with_recovery_hint("⚠ Couldn't read the cost ledger.")
        # → "⚠ Couldn't read the cost ledger.\\n\\n_If I'm still
        #     acting up — try /reset, or /resurrect to switch to a
        #     free local model._"
    """
    if not msg or not msg.strip():
        return msg or ""
    low = msg.lower()
    if "/reset" in low or "/resurrect" in low:
        return msg
    return f"{msg}\n\n{RECOVERY_HINT}"
