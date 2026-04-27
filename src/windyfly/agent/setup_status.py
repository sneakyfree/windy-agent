"""Centralized "what's configured?" introspection for grandma-mode.

Each integration with optional credentials (Gmail, Cloudflare, Google
Calendar, ...) has its own ``_is_configured`` check buried inside the
module. There's no single place the LLM (or anyone) can ask
"what's currently dormant?" without poking each one individually.

This module collects them. Two consumers:

  1. The dormant-refusal text inside each capability calls
     ``dormant_nudge(integration_key)`` to get a friendly, LLM-aimed
     hint string instead of a baked-in "run windy setup-foo" line.
  2. The ``setup.status`` capability (registered in capabilities/
     setup.py) calls ``get_setup_status()`` to give the LLM a single
     introspection pass — useful at conversation start when the bot
     wants to proactively offer setup ("I see Gmail isn't set up yet,
     want to fix that?").

The strings here are deliberately **NOT** user-facing language — they
are tool-result hints that the LLM rewrites for the user. The
critical instruction encoded in every nudge: ``Do NOT relay
developer-only setup paths to the user.`` The LLM-facing text
includes both the technical command (for power-user operators who
ARE on a terminal) and the chat-driven option (the grandma path).
The LLM is told to choose based on context.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypedDict


class IntegrationStatus(TypedDict):
    """Per-integration status entry returned by ``get_setup_status``."""

    key: str            # e.g. "gmail" — stable id for downstream tooling
    name: str           # e.g. "Gmail (sending email)"
    configured: bool    # is the credential present?
    setup_kinds: list[str]  # which setup paths exist: "cli" | "chat" | "env"
    cli_command: str | None  # e.g. "windy setup-gmail" (None if no CLI)
    chat_intent: str | None  # phrase user can say to start chat setup
    note: str | None    # extra detail (token scope, what unlocks, etc.)


def _gmail_configured() -> bool:
    # Mirror the check in capabilities.email (token file presence).
    token_path = Path(os.environ.get("GMAIL_TOKEN", "data/gmail_token.json"))
    return token_path.exists()


def _calendar_configured() -> bool:
    # Mirror the check in tools.calendar.
    token_path = Path(
        os.environ.get("GOOGLE_CALENDAR_TOKEN", "data/google_calendar_token.json")
    )
    return token_path.exists()


def _cloudflare_configured() -> bool:
    return bool(os.environ.get("CLOUDFLARE_API_TOKEN"))


def _github_configured() -> bool:
    return bool(
        os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_TOKEN")
    )


def get_setup_status() -> dict[str, Any]:
    """Snapshot which integrations are connected.

    Returned shape:
        {
          "summary": {"configured": 2, "dormant": 2, "total": 4},
          "integrations": [IntegrationStatus, ...],
          "dormant_keys": ["gmail", ...],
          "configured_keys": ["github", "cloudflare"],
        }

    Suitable for ``json.dumps`` straight to the LLM.
    """
    integrations: list[IntegrationStatus] = [
        {
            "key": "gmail",
            "name": "Gmail (sending email)",
            "configured": _gmail_configured(),
            "setup_kinds": ["cli", "chat"],
            "cli_command": "windy setup-gmail",
            "chat_intent": "set up email",
            "note": (
                "OAuth2 with gmail.send scope. CLI flow opens a browser; "
                "chat flow walks the user through cloudconsole.cloud.google.com "
                "step by step (preferred for non-technical users)."
            ),
        },
        {
            "key": "calendar",
            "name": "Google Calendar (read + create events)",
            "configured": _calendar_configured(),
            "setup_kinds": ["cli", "chat"],
            "cli_command": "windy setup-calendar",
            "chat_intent": "set up calendar",
            "note": (
                "OAuth2 with calendar scope. Same Google Cloud project as "
                "Gmail; different token file."
            ),
        },
        {
            "key": "cloudflare",
            "name": "Cloudflare (zones + DNS)",
            "configured": _cloudflare_configured(),
            "setup_kinds": ["chat", "env"],
            "cli_command": None,  # no dedicated CLI yet
            "chat_intent": "set up cloudflare",
            "note": (
                "API token (Bearer). User needs to create a token at "
                "dash.cloudflare.com/profile/api-tokens with Zone:Read + "
                "DNS:Read scopes (or DNS:Edit for write capabilities), "
                "then paste it in chat."
            ),
        },
        {
            "key": "github",
            "name": "GitHub (read + write via API)",
            "configured": _github_configured(),
            "setup_kinds": ["env"],
            "cli_command": None,
            "chat_intent": "set up github",
            "note": (
                "Personal Access Token in GITHUB_PAT or GITHUB_TOKEN env. "
                "Without it, public-repo reads still work but rate-limited."
            ),
        },
    ]

    configured_keys = [i["key"] for i in integrations if i["configured"]]
    dormant_keys = [i["key"] for i in integrations if not i["configured"]]

    return {
        "summary": {
            "configured": len(configured_keys),
            "dormant": len(dormant_keys),
            "total": len(integrations),
        },
        "integrations": integrations,
        "configured_keys": configured_keys,
        "dormant_keys": dormant_keys,
    }


def is_configured(key: str) -> bool:
    """Quick lookup: is one specific integration connected?"""
    for i in get_setup_status()["integrations"]:
        if i["key"] == key:
            return i["configured"]
    return False


def dormant_nudge(key: str) -> str:
    """LLM-aimed nudge string for a dormant integration.

    Returned text is read by the LLM as a tool error, NOT shown
    directly to the user. The bot's reply layer rephrases it. The
    string therefore encodes BOTH the technical command (for terminal
    users) AND the chat-driven path (for grandma), with an explicit
    instruction to the LLM to pick the right one.

    If the integration key isn't recognized, returns a generic
    catch-all string the LLM can still handle.
    """
    status = get_setup_status()
    integration = next(
        (i for i in status["integrations"] if i["key"] == key), None,
    )
    if integration is None:
        return (
            f"Integration '{key}' isn't recognized. The user can ask the "
            "operator to add it to the bot."
        )

    name = integration["name"]
    cli = integration["cli_command"]
    chat = integration["chat_intent"]

    parts = [f"{name} isn't set up yet."]
    parts.append(
        "INSTRUCTION TO THE LLM: do NOT relay terminal commands or env-var "
        "edits to the user verbatim. Most users are non-technical."
    )
    parts.append(
        f"Tell the user in plain language that the feature isn't connected "
        f"yet, then offer: \"want me to walk you through setting it up? "
        f"Just say '{chat}' if so.\" Keep it short and warm."
    )
    if cli:
        parts.append(
            f"(If the operator self-identifies as a developer, you may "
            f"mention `{cli}` — but only then. Default to chat-walkthrough.)"
        )
    return " ".join(parts)
