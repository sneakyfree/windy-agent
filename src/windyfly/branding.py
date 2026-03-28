"""Branding layer — determines whether this is HiFly (generic) or Windy Fly (ecosystem).

This file is the single source of truth for the fork identity.
HiFly: generic open-source agent framework.
Windy Fly: HiFly + Windy ecosystem integration.

To switch between them, change IS_WINDY_FLY below or set the
HIFLY_BRAND environment variable.

IMPORTANT: The "It's Alive!" hatching ceremony is a CORE HiFly feature.
It is hardcoded into the framework and plays for ALL descendants — HiFly,
Windy Fly, or any future fork.  This is the signature.  Non-negotiable.
"""

from __future__ import annotations

import os

# ── Fork identity ─────────────────────────────────────────────────────
# Set to True for Windy Fly (ecosystem fork), False for HiFly (generic)
IS_WINDY_FLY: bool = os.environ.get("HIFLY_BRAND", "windyfly") == "windyfly"

# ── Core HiFly constants (ALL forks inherit these) ────────────────────
BRAND_EMOJI = "🪰"
HAS_HATCHING_CEREMONY = True  # HARDCODED. Every fly hatches. Non-negotiable.

# ── Fork-specific constants ───────────────────────────────────────────
if IS_WINDY_FLY:
    BRAND_NAME = "Windy Fly"
    BRAND_TAGLINE = "Your AI. Your Rules. Your Ecosystem."
    BRAND_CLI = "windy"
    BRAND_URL = "windyfly.ai"
    BRAND_BOT_USER = "@windyfly:chat.windypro.com"
    BRAND_HOMESERVER = "https://chat.windypro.com"
    HAS_ECOSYSTEM = True
    HAS_MATRIX_AUTOPROVISION = True
else:
    BRAND_NAME = "HiFly"
    BRAND_TAGLINE = "Your AI. Your Rules."
    BRAND_CLI = "hifly"
    BRAND_URL = "github.com/sneakyfree/hifly"
    BRAND_BOT_USER = ""
    BRAND_HOMESERVER = ""
    HAS_ECOSYSTEM = False
    HAS_MATRIX_AUTOPROVISION = False
