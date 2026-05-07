"""First-message tour for brand-new bots.

Grandma's grandkids told her she needs an AI agent. She hatched
one. She types "hi" — and what happens? Pre-PR, the LLM improvised
a "Welcome back!" or some generic greeting that gave her no
orientation. PR #117 added a system-prompt FIRST CONTACT
instruction telling the LLM not to fake familiarity. This module
takes the next step: skip the LLM entirely on the first message
and ship a deterministic 5-bullet tour.

Why deterministic instead of LLM-driven:
  - Predictable: every grandma gets the same orientation. Word-of-
    mouth ("ask Windy '/help'") survives because everyone learns
    the same vocabulary.
  - Free: no LLM call burned on the welcome. The user's actual
    first question can be answered with the next message.
  - Survives broken creds: even if the Anthropic key is dead at
    hatch time, the welcome still works (no LLM dependency).
  - Bakes in the recovery story: /reset / /resurrect mentioned
    right at the top so she doesn't have to discover them later.

Trigger: ``is_first_contact(db)`` — episodes table AND nodes table
both empty. Same predicate that prompt.py used pre-PR; lifted into
this module so the agent loop can reuse it for the welcome
shortcut without importing prompt internals.

Integration point: ``agent_respond`` checks ``is_first_contact`` AFTER
the empty-message / pause / resurrection guards but BEFORE prompt
assembly + LLM dispatch. If true, it returns the welcome text and
saves both the user's message and the welcome reply as episodes —
so the NEXT message no longer triggers the welcome (episodes
table is no longer empty).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database


def is_first_contact(db: "Database") -> bool:
    """True iff the bot has zero prior memory of any kind.

    Detection: episodes AND nodes tables both empty. If either
    has rows, the bot has SOMETHING to anchor familiarity on (could
    be other-session history, extracted facts, the operator's seed
    data, etc.) and the welcome would feel out of place.

    Returns False on schema errors so a bot with a broken DB doesn't
    welcome-loop forever — better to fall through to the LLM and
    surface the real problem.
    """
    try:
        ep_row = db.fetchone("SELECT COUNT(*) AS c FROM episodes")
        nd_row = db.fetchone("SELECT COUNT(*) AS c FROM nodes")
    except Exception:
        return False
    n_eps = (ep_row or {}).get("c", 0)
    n_nodes = (nd_row or {}).get("c", 0)
    return n_eps == 0 and n_nodes == 0


# The exact welcome text. Hardcoded for predictability — every
# grandma gets the same orientation.
#
# Constraints:
#   - Under 4096 chars (Telegram single-message cap)
#   - Mention /reset and /resurrect explicitly (the recovery
#     vocabulary she'll see everywhere — PR #141 footer reuses it)
#   - Mention tap-/ for the menu (PR #139 surface)
#   - Mention voice notes (PR #129 surface) so non-typists know
#   - Sound like a person, not a manual
WELCOME_TEXT = (
    "🪰 *Hi! I'm Windy Fly — your personal AI companion.*\n\n"
    "I just hatched. Five things to know:\n\n"
    "🆘 If I stop responding — type /resurrect or /reset.\n"
    "💬 Tap / for a menu of things I can do.\n"
    "🧠 Tell me about yourself — I remember everything you share.\n"
    "💰 Worried about cost? Type /spend any time.\n"
    "🎙 You can send me a voice note instead of typing.\n\n"
    "What's on your mind?"
)


def format_welcome() -> str:
    """Return the welcome text. Function form (not constant) so
    future per-language / per-band variants can be added without
    breaking callers."""
    return WELCOME_TEXT
