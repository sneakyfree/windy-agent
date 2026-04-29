"""Capability Plane — band-gated, sandbox-aware tool registration.

The Capability Plane is the architectural choke point for everything
the agent can do that touches the world. Every tool — present or
future — registers as a Capability with explicit metadata about who
can call it (band), where it runs (sandbox tier), how reversible it
is, and what its blast radius looks like. This is the slot every
"hand" plugs into in Waves 3-5.

See ``project_windy_fly_architecture.md`` in user memory for the
strategic framing. Wave 2 #1 (this scaffold) just establishes the
shape; Wave 2 #2 adds the action audit ledger; Wave 2 #3 migrates
existing tools.
"""

from windyfly.agent.capabilities.descriptor import (
    Band,
    Capability,
    CapabilityDenied,
    CapabilityTimeout,
    Reversibility,
    SandboxTier,
    Tier,
    defaults_for_tier,
)
from windyfly.agent.capabilities.audit import (
    get_current_session_id,
    install_audit_hooks,
    set_current_session_id,
)
from windyfly.agent.capabilities.registry import (
    CapabilityRegistry,
    capability_registry,
)


# Back-compat re-export for the legacy ``capabilities.py`` module that
# this package shadowed when Wave 2 #1 introduced the package directory.
# The /capabilities slash command in commands/core.py imports HELP_TEXT
# from this path; without this re-export, that import would fail at
# runtime. Keep until Grant decides whether to migrate the legacy
# module's content into the new author guide.
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
  • /version — check for updates

🔧 New (Capability Plane)
  • /caps — list capabilities the agent can call directly
  • /pulse — live runtime diagnostic"""

__all__ = [
    "Band",
    "Capability",
    "CapabilityDenied",
    "CapabilityTimeout",
    "CapabilityRegistry",
    "HELP_TEXT",
    "Reversibility",
    "SandboxTier",
    "Tier",
    "capability_registry",
    "defaults_for_tier",
    "install_audit_hooks",
]
