"""Channel-agnostic slash-command recognition.

Telegram, Matrix, iMessage, WhatsApp — they all let users type
``/pause`` / ``/version`` / ``/reset`` / etc. The string-recognition
logic for those is identical across channels. Pre-PR #130 it lived
in ``telegram_bot.py`` only; copy-paste was waiting to happen the
moment Matrix tried to add slash-command support.

This module is the channel-agnostic recognition layer. Each
function takes a message string, returns True/False (or for the
more complex commands, a parsed tuple). Pure text — no I/O, no
DB, no LLM. Channel adapters consume these to decide whether to
short-circuit before dispatching to the agent loop.

What's intentionally NOT here:
  - The reply text. Channel adapters format their own ack
    replies because Markdown / mention-formatting / emoji
    rendering all vary by channel.
  - The actual side effects (writing the pause flag, restarting
    the bot, etc.). That stays in the channel adapter so the
    adapter controls timing and ordering.
  - The yolo and guest parsers, which return more complex
    payloads. Currently TG-only; will move here when a second
    channel needs them.
"""

from __future__ import annotations


# ── Nuclear reset — exact + phrase match ──────────────────────────


PANIC_EXACT = frozenset({
    "/reset", "/panic", "/nuclear", "🆘",
})

PANIC_PHRASES = (
    "reset my agent",
    "nuclear reset",
    "factory reset",
    "bring my agent back",
    "bring back my agent",
    "my agent is broken",
    "my bot is broken",
    "agent is stuck",
    "bot is stuck",
)


def is_panic_message(text: str | None) -> bool:
    """True iff text is a /reset trigger (exact match) OR contains
    one of the phrase forms (e.g., 'my bot is broken'). Phrase match
    catches grandma-mode phrasing where the user types in plain
    English instead of remembering the slash command."""
    if not text:
        return False
    low = text.strip().lower()
    if low in PANIC_EXACT:
        return True
    return any(p in low for p in PANIC_PHRASES)


# ── Spend kill-switch ─────────────────────────────────────────────


PAUSE_EXACT = frozenset({"/pause", "/stop-spending", "/stop"})
RESUME_EXACT = frozenset({"/resume", "/wake-up", "/wake"})
SPEND_EXACT = frozenset({"/spend", "/usage", "/burn"})


def is_pause_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in PAUSE_EXACT


def is_resume_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in RESUME_EXACT


def is_spend_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in SPEND_EXACT


# ── Introspection ─────────────────────────────────────────────────


VERSION_EXACT = frozenset({"/version", "/v"})
UPTIME_EXACT = frozenset({"/uptime"})
WHOAMI_EXACT = frozenset({"/whoami", "/identity"})


# ── Resurrect / lifeboat — last-resort recovery ───────────────────
#
# When all paid providers are dead (Anthropic key revoked, OpenAI
# rate-limited, etc.), the user hits one of these to switch the bot
# to a free local model so they can keep talking while they fix
# their credentials. PR #133.

RESURRECT_EXACT = frozenset({
    "/resurrect", "/save-me", "/lifeboat", "/sos",
})

# Phrase matches anywhere in a longer message — grandma-mode entry
# points for users who don't remember the slash command.
RESURRECT_PHRASES = (
    "bring me back",
    "bring me back alive",
    "bring me back to life",
    "save me",
    "are you alive",
    "are you there",
    "i can't reach you",
    "are you dead",
)

NORMAL_EXACT = frozenset({
    "/normal", "/normal-mode", "/back-to-normal",
})

# Auto-resurrect toggle (PR #145). Default ON; user can opt-out.
# Surface: /auto-resurrect on|off|status (or bare /auto-resurrect = status)
AUTO_RESURRECT_PREFIXES = ("/auto-resurrect", "/auto-rescue", "/autoresurrect")


def parse_auto_resurrect_command(text: str | None) -> tuple[bool, str | None]:
    """Returns (is_cmd, arg) where arg is one of:
      - None       → bare command → show status
      - "on"
      - "off"
      - "invalid"  → unrecognized arg
    """
    if not text:
        return False, None
    t = text.strip().lower()
    for prefix in AUTO_RESURRECT_PREFIXES:
        if t == prefix:
            return True, None
        if t.startswith(prefix + " "):
            arg = t[len(prefix) + 1:].strip()
            if arg in ("on", "enable", "yes"):
                return True, "on"
            if arg in ("off", "disable", "no"):
                return True, "off"
            if arg in ("status", "?"):
                return True, None
            return True, "invalid"
    return False, None


LIFEBOAT_STATUS_EXACT = frozenset({
    "/lifeboat", "/lifeboat-status", "/lifeboatstatus",
})


def is_lifeboat_status_message(text: str | None) -> bool:
    """True iff text is a lifeboat-status query (read-only, never
    mutates flags). Distinct from /resurrect (toggles ON) and
    /normal (toggles OFF)."""
    if not text:
        return False
    return text.strip().lower() in LIFEBOAT_STATUS_EXACT


def is_resurrect_message(text: str | None) -> bool:
    """True iff text triggers lifeboat mode (exact slash match OR a
    phrase like 'bring me back alive')."""
    if not text:
        return False
    low = text.strip().lower()
    if low in RESURRECT_EXACT:
        return True
    return any(p in low for p in RESURRECT_PHRASES)


def is_normal_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in NORMAL_EXACT


def is_version_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in VERSION_EXACT


def is_uptime_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in UPTIME_EXACT


def is_whoami_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in WHOAMI_EXACT


# ── /goal (Phase 1: persistent objective, two-model evaluator) ────
#
# windy-agent parity with Claude Code 2.1.139's /goal and Codex CLI
# / Hermes Agent 0.13.0's identical command. Surface:
#
#   /goal <text>     — set or replace the active goal
#   /goal            — show status (alias: /goal status)
#   /goal status     — show status
#   /goal clear      — abandon active goal
#   /goal done       — mark active goal complete
#
# Returns (is_cmd, subcommand, text_arg) where subcommand is one
# of {"set", "status", "clear", "done", "invalid"} and text_arg is
# the goal text when subcommand=="set" (else None).
GOAL_PREFIXES = ("/goal", "/objective", "/mission")
_GOAL_STATUS_WORDS = frozenset({"status", "show", "?"})
_GOAL_CLEAR_WORDS = frozenset({"clear", "cancel", "abandon", "stop", "reset"})
_GOAL_DONE_WORDS = frozenset({"done", "complete", "finished", "finish"})


def parse_goal_command(
    text: str | None,
) -> tuple[bool, str | None, str | None]:
    """Parse ``/goal [...]`` slash command.

    Returns ``(is_cmd, subcommand, text_arg)`` where:
      - ``is_cmd`` — True iff message starts with /goal (or alias)
      - ``subcommand`` — one of "set", "status", "clear", "done", "invalid"
      - ``text_arg`` — the new goal text when subcommand == "set"
    """
    if not text:
        return False, None, None
    t = text.strip()
    if not t:
        return False, None, None

    lower = t.lower()
    matched_prefix = None
    for prefix in GOAL_PREFIXES:
        if lower == prefix:
            return True, "status", None
        if lower.startswith(prefix + " "):
            matched_prefix = prefix
            break
    if matched_prefix is None:
        return False, None, None

    # Everything after the prefix (preserving original case for the
    # goal text — users type "Plan my Yellowstone trip" not "plan...").
    arg = t[len(matched_prefix) + 1:].strip()
    if not arg:
        return True, "status", None

    arg_lower = arg.lower()
    if arg_lower in _GOAL_STATUS_WORDS:
        return True, "status", None
    if arg_lower in _GOAL_CLEAR_WORDS:
        return True, "clear", None
    if arg_lower in _GOAL_DONE_WORDS:
        return True, "done", None

    # /goal pace <duration> — Phase 2 timer pacing. Parsed here
    # because the subcommand has its own argument format. Returns
    # ("pace", <seconds-as-int-string-or-'off'>).
    if arg_lower.startswith("pace"):
        rest = arg[4:].strip()
        if not rest or rest.lower() in ("status", "?"):
            return True, "pace_status", None
        if rest.lower() in ("off", "no", "stop", "disable", "0"):
            return True, "pace_set", "0"
        # Duration parse: 30m, 1h, 4h, 8h, daily, or bare seconds
        seconds = _parse_duration(rest)
        if seconds is None:
            return True, "pace_invalid", rest
        return True, "pace_set", str(seconds)

    # Anything else is treated as setting a new goal. Including
    # multi-word objectives, sentences with question marks, etc.
    return True, "set", arg


def _parse_duration(text: str) -> int | None:
    """Parse user-friendly durations to seconds. Accepts:
      - ``30m`` / ``30min`` / ``30minutes``
      - ``2h`` / ``2hr`` / ``2hour`` / ``2hours``
      - ``daily`` / ``hourly``
      - bare integer (treated as seconds — power-user escape hatch)
    Returns None on parse failure.
    """
    t = text.strip().lower()
    if t == "daily":
        return 24 * 60 * 60
    if t == "hourly":
        return 60 * 60
    # Numeric prefix + optional unit. The unit-table avoids the
    # rstrip("s") trap where "s" plural-stripped becomes "" and
    # silently misses the seconds case.
    import re
    m = re.match(
        r"^(\d+)\s*(s|sec|secs|second|seconds|"
        r"m|min|mins|minute|minutes|"
        r"h|hr|hrs|hour|hours)?$",
        t,
    )
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or "s"
    multipliers = {
        "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    }
    if unit not in multipliers:
        return None
    return n * multipliers[unit]
