"""`windy commands --json`: the command registry as data, so nobody hand-copies it.

One row per command: name, description, usage, category, aliases, dangerous, remote_allowed (usable
from a remote chat such as Matrix/Telegram: owner only), ecosystem_only. A phone or another engine can
read this instead of a hard-coded list. The SHARED command set that both engines implement is a
windy-contracts file owned by Windy Chat; this export is windyfly's side of it.
"""

from __future__ import annotations

from typing import Any


def commands_json() -> list[dict[str, Any]]:
    from windyfly.commands import core
    from windyfly.commands.registry import _REMOTE_ALLOWED_CATEGORIES, registry

    core.init_core()  # registers every command (no db/config needed just to list them)
    return [
        {
            "name": c.name,
            "description": c.description,
            "usage": c.usage or c.name,
            "category": c.category,
            "aliases": sorted(c.aliases),
            "dangerous": bool(c.dangerous),
            "remote_allowed": c.category in _REMOTE_ALLOWED_CATEGORIES,
            "ecosystem_only": bool(c.ecosystem_only),
        }
        for c in registry.all()
    ]
