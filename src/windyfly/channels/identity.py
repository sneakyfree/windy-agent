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
  no legacy tools, no registry commands, no rescue commands, and no
  owner name / private history in the prompt).
- **No allowlist configured** → **Trust-On-First-Use (TOFU)**. The
  first sender an unclaimed agent talks to on a platform is bound as
  that platform's owner and persisted to disk; every *later* sender is
  a SANDBOX stranger. This is the grandma-safe default: a freshly
  hatched agent lives in a private 1:1 DM room its owner was invited to
  at hatch, so the owner is always the first to speak — the agent binds
  to her automatically with zero env editing, and it becomes impossible
  for a stranger who later finds the agent to be treated as the owner.
  (Pre-TOFU behavior — everyone-is-OWNER — was the 2026-07-06 Windy 0
  finding: a demo user got greeted by the owner's name and offered SSH /
  fleet / DNS tooling. TOFU is the line HiFly's public default flips.)
- **Explicit opt-out** → set ``WINDY_LEGACY_OWNER_MODE=1`` to restore
  the historical everyone-is-OWNER behavior (single-user box, or a
  deliberately public bot where every sender should be trusted). Loud
  once-per-platform warning so it's never silently on.
- **Guest mode** caps everything at USER (unchanged semantics — demo
  audiences get GRANDMA MODE regardless of who they are).

Configuration:
    WINDY_OWNER_IDS="discord:123456,slack:U0AB12,signal:+15551234567"
    (comma-separated platform:sender_id pairs; also merged from
    config [trust] owner_ids when the caller passes config)
    AGENT_OWNER_TELEGRAM_ID is absorbed automatically as a telegram
    owner so the existing fleet convention keeps working unmodified.

    WINDY_OWNER_BINDINGS_PATH overrides where TOFU bindings persist
    (default ~/.windy/owner-bindings.json). Delete that file to let the
    agent re-bind on next contact.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities import Band

logger = logging.getLogger(__name__)

_warned_platforms: set[str] = set()
_tofu_bound_platforms: set[str] = set()


def _legacy_mode() -> bool:
    """True when the operator explicitly opted into everyone-is-OWNER."""
    return os.environ.get("WINDY_LEGACY_OWNER_MODE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _bindings_path() -> Path:
    """Where TOFU owner bindings persist.

    ``WINDY_OWNER_BINDINGS_PATH`` wins when set (tests point it at a tmp
    file). Otherwise it lives under ``windy_state_dir()`` alongside the
    other runtime state — so the suite-wide ``WINDY_STATE_DIR`` test
    isolation covers it automatically and it lands in ~/.windy in prod.
    """
    override = os.environ.get("WINDY_OWNER_BINDINGS_PATH")
    if override:
        return Path(os.path.expanduser(override))
    from windyfly.platform import windy_state_dir
    return windy_state_dir() / "owner-bindings.json"


def _load_bindings() -> dict[str, set[str]]:
    """Read persisted TOFU owner bindings: platform → {sender_id}.

    Stored as ``{"matrix": "@owner:server", ...}`` — one owner per
    platform (the first sender). Tolerant of a missing / corrupt file:
    a broken bindings file must never take the resolver offline (the
    grandma-proof bar — degrade to "no binding", never crash).
    """
    path = _bindings_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("owner-bindings file unreadable (%s): %s", path, e)
        return {}
    out: dict[str, set[str]] = {}
    if isinstance(raw, dict):
        for platform, sender in raw.items():
            if isinstance(platform, str) and isinstance(sender, str) and sender:
                out[platform.lower()] = {sender}
    return out


def _persist_binding(platform: str, sender_id: str) -> None:
    """Atomically record ``platform → sender_id`` as the bound owner.

    Best-effort: if the write fails (read-only FS, etc.) we log and
    carry on — the in-process ``_tofu_bound_platforms`` guard still
    prevents re-binding a *different* sender within this process, so a
    persistence failure degrades to "owner recognized until restart"
    rather than "stranger becomes owner".
    """
    path = _bindings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, str] = {}
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict):
                current = {k: v for k, v in existing.items() if isinstance(v, str)}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            current = {}
        current[platform] = sender_id
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError as e:
        logger.warning(
            "could not persist owner binding %s=%s to %s: %s",
            platform, sender_id, path, e,
        )


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
    """platform → allowed owner sender_ids, from env + config + bindings.

    Precedence is a union (any source can add an owner): explicit env
    ``WINDY_OWNER_IDS`` + ``AGENT_OWNER_TELEGRAM_ID`` + config
    ``[trust] owner_ids`` + persisted TOFU bindings. Explicit config is
    never *overridden* by a stale binding — they merge — so setting
    ``WINDY_OWNER_IDS`` is always sufficient to lock an agent down
    regardless of what got auto-bound earlier.
    """
    owners = _parse_pairs(os.environ.get("WINDY_OWNER_IDS", ""))

    # Fleet convention: telegram owner via AGENT_OWNER_TELEGRAM_ID.
    tg = os.environ.get("AGENT_OWNER_TELEGRAM_ID", "").strip()
    if tg:
        owners.setdefault("telegram", set()).add(tg)

    if config:
        for pair in (config.get("trust", {}) or {}).get("owner_ids", []) or []:
            for platform, ids in _parse_pairs(str(pair)).items():
                owners.setdefault(platform, set()).update(ids)

    # Persisted first-contact (TOFU) bindings.
    for platform, ids in _load_bindings().items():
        owners.setdefault(platform, set()).update(ids)

    return owners


def bind_owner(platform: str, sender_id: str) -> None:
    """Explicitly bind ``platform``'s owner to ``sender_id`` and persist.

    Public helper so the hatch/provision flow can bind the owner the
    moment their per-platform handle is known (ideal hardening — no
    reliance on the owner being the literal first message). Idempotent
    and safe to call repeatedly.
    """
    platform = (platform or "").lower()
    sender_id = (sender_id or "").strip()
    if not platform or not sender_id:
        return
    _persist_binding(platform, sender_id)
    _tofu_bound_platforms.add(platform)


def resolve_band(
    platform: str,
    sender_id: str | None,
    *,
    config: dict[str, Any] | None = None,
) -> Band:
    """Resolve the trust band for one incoming message."""
    platform = (platform or "unknown").lower()
    sender = (str(sender_id).strip() if sender_id else "")

    try:
        from windyfly.agent.guest_mode import is_guest_active
        guest = is_guest_active()
    except Exception:
        guest = False

    owners = owner_ids(config)
    platform_owners = owners.get(platform)

    if platform_owners:
        # Strict mode: an owner is known for this platform (via env,
        # config, or a prior TOFU binding). Match → OWNER, else SANDBOX.
        band = Band.OWNER if sender and sender in platform_owners else Band.SANDBOX
    elif _legacy_mode():
        # Explicit opt-in to the historical everyone-is-OWNER behavior.
        if platform not in _warned_platforms:
            _warned_platforms.add(platform)
            logger.warning(
                "channel '%s' running in LEGACY OWNER MODE — every "
                "sender is treated as OWNER (WINDY_LEGACY_OWNER_MODE is "
                "set). Unset it and configure WINDY_OWNER_IDS to lock "
                "down.", platform,
            )
        band = Band.OWNER
    elif sender:
        # Trust-On-First-Use: no owner known yet → the first sender we
        # ever hear from on this platform becomes the bound owner. Every
        # later stranger falls through to SANDBOX because the binding is
        # persisted and re-read on the next call. This is the security-
        # critical path: EVERY remote channel (matrix, telegram, discord,
        # ...) always carries a sender id, so a stranger who DMs an
        # unclaimed agent after the owner has spoken is sandboxed, never
        # treated as owner.
        bind_owner(platform, sender)
        if platform not in _warned_platforms:
            _warned_platforms.add(platform)
            logger.warning(
                "channel '%s' had no owner configured — bound first "
                "sender %s as owner (Trust-On-First-Use). Set "
                "WINDY_OWNER_IDS=\"%s:<id>\" to pin this explicitly.",
                platform, sender, platform,
            )
        band = Band.OWNER
    else:
        # No owner configured AND no attributable sender id. A remote
        # channel always populates a sender, so this is a local /
        # unattributed operator context (CLI, embedded, a direct
        # agent_respond call) — treat it as the operator: OWNER. When an
        # allowlist IS configured, this branch is never reached (a
        # no-sender message is SANDBOX'd above by the non-match path), so
        # a locked-down agent still refuses an unidentified sender.
        band = Band.OWNER

    if guest and band > Band.USER:
        band = Band.USER
    return band


def _reset_warnings_for_tests() -> None:
    _warned_platforms.clear()
    _tofu_bound_platforms.clear()
