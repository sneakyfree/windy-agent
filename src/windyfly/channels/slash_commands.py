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
