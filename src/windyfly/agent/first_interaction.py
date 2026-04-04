"""First interaction magic — make the agent's first response memorable.

On the very first message, the agent introduces itself with personality,
references the user by name, and demonstrates a capability. After the
first interaction, the flag is set and normal behavior resumes.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


def is_first_interaction(db: Database, user_id: str = "default") -> bool:
    """Check if this is the user's first ever interaction."""
    row = db.fetchone(
        "SELECT value FROM soul WHERE key = 'first_interaction_done' AND user_id = ?",
        (user_id,),
    )
    return row is None


def mark_first_interaction_done(db: Database, user_id: str = "default") -> None:
    """Mark the first interaction as completed."""
    from windyfly.memory.soul import upsert_soul
    upsert_soul(db, key="first_interaction_done", value="true", source="system", user_id=user_id)


def get_first_interaction_prompt(user_message: str, config: dict) -> str | None:
    """Generate a special system prompt for the first interaction.

    Returns None if this isn't the first interaction or if the message
    doesn't warrant special treatment.
    """
    owner_name = os.environ.get("WINDY_OWNER_NAME", "")
    agent_name = config.get("agent", {}).get("name", "Windy Fly")

    greeting = _is_greeting(user_message)

    if greeting and owner_name:
        return (
            f"THIS IS YOUR VERY FIRST CONVERSATION with {owner_name}. "
            f"Make it memorable! Introduce yourself as {agent_name}. "
            f"Greet {owner_name} by name warmly. "
            "Then proactively demonstrate ONE of your capabilities — "
            "for example, check the weather for their area, or mention "
            "you can set reminders, manage their to-do list, search the web, "
            "and check the news. Be specific: 'Want me to check the weather, "
            "set a reminder, or help with your to-do list?' "
            "Don't give a generic 'How can I help you?' — show what you can DO."
        )
    elif greeting:
        return (
            f"THIS IS YOUR VERY FIRST CONVERSATION. Make it memorable! "
            f"Introduce yourself as {agent_name} with personality. "
            "Immediately demonstrate value — mention specific things you can do: "
            "weather, reminders, to-dos, news, web search, email, calculations. "
            "Be proactive and specific. Don't just say 'How can I help?' — "
            "offer to do something concrete right now."
        )
    else:
        # Non-greeting first message — answer it, but add a brief intro
        return (
            f"This is your first interaction. Answer the user's question fully, "
            f"then briefly introduce yourself as {agent_name} and mention "
            "one or two other things you can help with."
        )


def _is_greeting(message: str) -> bool:
    """Check if a message is a simple greeting."""
    greetings = {"hi", "hello", "hey", "sup", "yo", "heya", "hiya",
                 "good morning", "good afternoon", "good evening",
                 "what's up", "howdy", "hola"}
    clean = message.strip().lower().rstrip("!.?")
    return clean in greetings or len(clean) < 5


# ── Capability nudge after N tool-free interactions ──────────────

_NUDGE_THRESHOLD = 5


def should_nudge_capabilities(db: Database, user_id: str = "default") -> bool:
    """Check if we should suggest capabilities to the user.

    Returns True if the user has had 5+ interactions without using any tools.
    Only nudges once.
    """
    # Check if already nudged
    nudged = db.fetchone(
        "SELECT value FROM soul WHERE key = 'capabilities_nudged' AND user_id = ?",
        (user_id,),
    )
    if nudged:
        return False

    # Count interactions
    episode_count = db.fetchone(
        "SELECT COUNT(*) as cnt FROM episodes WHERE role = 'user'",
    )
    if not episode_count or episode_count["cnt"] < _NUDGE_THRESHOLD:
        return False

    # Check if any tools were used (look for tool-related events)
    tool_events = db.fetchone(
        "SELECT COUNT(*) as cnt FROM events WHERE event_type LIKE 'tool.%'",
    )
    if tool_events and tool_events["cnt"] > 0:
        return False

    return True


def mark_capabilities_nudged(db: Database, user_id: str = "default") -> None:
    """Mark that we've nudged the user about capabilities."""
    from windyfly.memory.soul import upsert_soul
    upsert_soul(db, key="capabilities_nudged", value="true", source="system", user_id=user_id)


def get_capability_nudge() -> str:
    """Get the capability nudge text to inject into the system prompt."""
    return (
        "The user has been chatting for a while but hasn't tried any tools yet. "
        "At the end of your next response, gently mention: "
        "'By the way, did you know I can check the weather, set reminders, "
        "manage your to-do list, search the web, and more? Just ask!' "
        "Only mention this once — don't repeat it."
    )
