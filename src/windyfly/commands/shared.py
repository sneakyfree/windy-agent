"""The shared owner command set (windy-contracts ``schema/chat/commands.v1.json``, owner: windy-chat).

One ecosystem list of slash commands, the same names/args/help on every engine (Chat's roster and
windyfly). This is a MIRROR of the contract's ``x-commands``: Chat owns the wording, so a change is a
windy-contracts PR and then a copy here (the conformance test fails loudly if a name stops resolving).
windyfly's other commands are not part of this set and stay as they are (``/commands`` lists them all).
"""

from __future__ import annotations

ENGINE = "windyfly"

# (name, args, help, supported_by)
SHARED: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("help", "", "List what I understand.", ("roster", "windyfly")),
    ("status", "", "Am I healthy? My model, my mailbox, what I can use right now.", ("roster", "windyfly")),
    ("whoami", "", "My name, passport, owner and email.", ("roster", "windyfly")),
    ("model", "[sonnet|opus|default|<model id>]", "Show or change the model I use.", ("roster", "windyfly")),
    ("usage", "", "What I have used lately (tokens and cost), from Windy Mind.", ("roster", "windyfly")),
    ("pause", "", "Stop answering anything except your commands until /resume.", ("roster", "windyfly")),
    ("resume", "", "Start answering again.", ("roster", "windyfly")),
    ("new", "", "Start a fresh conversation (I forget what was said above this line).", ("roster", "windyfly")),
    ("undo", "", "Undo the last change I made for you, where that can be undone.", ("windyfly",)),
    ("memory", "[search words]", "What I remember about you.", ("windyfly",)),
    ("forget", "<what>", "Make me forget something I remember.", ("windyfly",)),
    ("agents", "[on|off]", "Show or change whether OTHER agents (not your own) may talk to me.", ("roster", "windyfly")),
)


def mine() -> list[tuple[str, str, str]]:
    """(name, args, help) of the shared commands this engine implements."""
    return [(n, a, h) for n, a, h, who in SHARED if ENGINE in who]


def help_text(exists=lambda name: True) -> str:
    """``/help`` for the owner: ``/<name> <args>: <help>`` per shared command this engine really has
    (``exists(name)``), then where the rest is."""
    lines = [f"/{n}{' ' + a if a else ''}: {h}" for n, a, h in mine() if exists(n)]
    lines.append("/commands: Every command I have.")
    return "\n".join(lines)
