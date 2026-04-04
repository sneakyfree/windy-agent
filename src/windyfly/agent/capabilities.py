"""Capability discovery — help users discover what the agent can do.

Provides the /help output and alternative suggestions when the agent
can't do something.
"""

from __future__ import annotations

HELP_TEXT = """🪰 What I Can Do:

📋 Productivity
  • "Remind me to..." — set reminders and timers
  • "Add [item] to my list" — manage to-dos
  • "What's on my list?" — view to-dos
  • "What's on my calendar?" — check events (needs Google Calendar setup)

🌤️  Information
  • "What's the weather in [city]?" — current weather anywhere
  • "Latest news about [topic]" — headlines from major outlets
  • "Search for [query]" — web search
  • "Read this article: [URL]" — fetch and summarize web pages

🧮 Utilities
  • "What's 15% of 230?" — calculator
  • "Convert 10 km to miles" — unit conversion
  • "Set a timer for 20 minutes" — countdown timer
  • "Flip a coin" / "Roll a d20" — random

📧 Communication
  • "Email [person] about [topic]" — send email (needs Windy Mail)
  • "Text [person] [message]" — send SMS (needs Twilio)

💬 Just Chat
  • Talk to me about anything — I remember everything!
  • I learn your preferences and improve over time

⚙️ Management
  • /personality — adjust my humor, warmth, autonomy
  • /budget — check spending vs daily limit
  • /ecosystem — see connected services
  • /version — check for updates"""


ALTERNATIVES: dict[str, str] = {
    "call": "I can't make phone calls yet, but I can send a text message instead. Want me to text them?",
    "phone call": "I can't make phone calls yet, but I can send a text message instead. Want me to text them?",
    "order": "I can't place orders directly, but I can search for what you need and add it to your to-do list.",
    "buy": "I can't make purchases, but I can search for the best deals and add it to your shopping list.",
    "play music": "I can't play music yet, but I can search for songs or set a reminder to listen later.",
    "alarm": "I can set a reminder for you instead! When would you like to be reminded?",
    "calendar": "I don't have calendar access set up yet. Want me to set a reminder instead? Or run `windy setup-calendar` to connect Google Calendar.",
    "photo": "I can't take or view photos, but I can help you describe, organize, or search for images online.",
    "map": "I can't show maps, but I can look up directions or weather for any location.",
    "drive": "I can't access Google Drive directly, but I can help you organize tasks or search for files online.",
}


def get_alternative_suggestion(user_message: str) -> str | None:
    """If the user asks for something we can't do, suggest what we CAN do."""
    msg_lower = user_message.lower()
    for trigger, suggestion in ALTERNATIVES.items():
        if trigger in msg_lower:
            return suggestion
    return None
