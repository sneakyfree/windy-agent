"""Sender identity → band resolution for every channel (Sprint 4).

The 2026-07-04 audit's public-launch disqualifier: only telegram
checked WHO was talking. Discord/Slack/Signal/WhatsApp/Teams/IRC
accepted any sender and every channel ran turns at Band.OWNER — a
stranger who found the Signal number had the owner's full toolset.
Band gating existed in the Capability Plane but was theater because
nothing ever resolved a real band.

This module is the resolver. Policy (OpenClaw's session-trust lesson,
adapted):

- **Owner allowlist configured for the platform** → matching senders
  get OWNER; everyone else gets SANDBOX (they can chat — the model
  still answers — but they see only Tier-0 pure-compute capabilities,
  no legacy tools, no registry commands, no rescue commands).
- **No allowlist configured for the platform** → legacy behavior
  (OWNER for all senders) with a loud once-per-platform warning, so
  existing single-user deploys don't break the day this ships.
  HiFly's public default should flip this to strict.
- **Guest mode** caps everything at USER (unchanged semantics — demo
  audiences get GRANDMA MODE regardless of who they are).

Configuration:
    WINDY_OWNER_IDS="discord:123456,slack:U0AB12,signal:+15551234567"
    (comma-separated platform:sender_id pairs; also merged from
    config [trust] owner_ids when the caller passes config)
    AGENT_OWNER_TELEGRAM_ID is absorbed automatically as a telegram
    owner so the existing fleet convention keeps working unmodified.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from windyfly.agent.capabilities import Band

logger = logging.getLogger(__name__)

_warned_platforms: set[str] = set()


def _parse_pairs(raw: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        platform, _, sender = pair.partition(":")
        platform, sender = platform.strip().lower(), sender.strip()
        if platform and sender:
            out.setdefault(platform, set()).add(sender)
    return out


def owner_ids(config: dict[str, Any] | None = None) -> dict[str, set[str]]:
    """platform → allowed owner sender_ids, from env + config."""
    owners = _parse_pairs(os.environ.get("WINDY_OWNER_IDS", ""))

    # Fleet convention: telegram owner via AGENT_OWNER_TELEGRAM_ID.
    tg = os.environ.get("AGENT_OWNER_TELEGRAM_ID", "").strip()
    if tg:
        owners.setdefault("telegram", set()).add(tg)

    if config:
        for pair in (config.get("trust", {}) or {}).get("owner_ids", []) or []:
            for platform, ids in _parse_pairs(str(pair)).items():
                owners.setdefault(platform, set()).update(ids)
    return owners


def resolve_band(
    platform: str,
    sender_id: str | None,
    *,
    config: dict[str, Any] | None = None,
) -> Band:
    """Resolve the trust band for one incoming message."""
    platform = (platform or "unknown").lower()

    try:
        from windyfly.agent.guest_mode import is_guest_active
        guest = is_guest_active()
    except Exception:
        guest = False

    owners = owner_ids(config)
    platform_owners = owners.get(platform)

    if not platform_owners:
        # Legacy mode: nothing configured for this platform. Keep the
        # historical everyone-is-owner behavior but say so loudly ONCE
        # per platform per process — this is the line HiFly must flip.
        if platform not in _warned_platforms:
            _warned_platforms.add(platform)
            logger.warning(
                "channel '%s' has NO owner allowlist — every sender is "
                "treated as OWNER (legacy mode). Set WINDY_OWNER_IDS="
                "\"%s:<your-sender-id>\" to lock it down.",
                platform, platform,
            )
        band = Band.OWNER
    elif sender_id and str(sender_id).strip() in platform_owners:
        band = Band.OWNER
    else:
        band = Band.SANDBOX

    if guest and band > Band.USER:
        band = Band.USER
    return band


def _reset_warnings_for_tests() -> None:
    _warned_platforms.clear()
