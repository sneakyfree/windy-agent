"""Who is sending: the agent's name, its owner, its passport.

Outbound messages to THIRD PARTIES (email, SMS) must say they come from
an AI agent acting for a named person (legal review, 2026-09-23). The
tools have no config handle at call time, so boot registers the config
here once and the footers read it back.
"""

from __future__ import annotations

import os
from typing import Any

_config: dict[str, Any] = {}


def set_config(config: dict[str, Any] | None) -> None:
    global _config
    _config = dict(config or {})


def agent_name() -> str:
    name = (_config.get("agent") or {}).get("name") or os.environ.get("WINDYFLY_AGENT_NAME", "")
    return str(name).strip() or "Windy Fly"


def owner_name() -> str:
    name = os.environ.get("WINDY_OWNER_NAME", "") or (_config.get("owner") or {}).get("name", "")
    return str(name).strip()


def owner_first_name() -> str:
    parts = owner_name().split()
    return parts[0] if parts else ""


def passport() -> str:
    return os.environ.get("ETERNITAS_PASSPORT", "").strip()


def email_footer() -> str:
    owner = owner_name()
    acting = f"acting for {owner}" if owner else "acting for its owner"
    return f"Sent by {agent_name()}, an AI agent {acting}."


def sms_footer() -> str:
    first = owner_first_name()
    who = f"AI assistant for {first}" if first else "AI assistant"
    return f"— {agent_name()}, {who}. Reply STOP to opt out."
