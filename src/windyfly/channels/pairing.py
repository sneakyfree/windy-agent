"""Owner pairing — how an agent learns who its owner is on a platform.

Replaces Trust-On-First-Use (see ``windyfly.channels.identity``). The
owner signs in to the dashboard with their hub login; the gateway asks
the agent (UDS ``owner.pair.create``) for a one-time code; the owner
sends ``/pair <code>`` to the agent on Telegram, Matrix, Discord, … and
that sender becomes the platform's bound owner.

Security properties:
- Only ``sha256(code)`` is persisted, never the code, and the code is
  never logged.
- Codes are single-use, expire (default 10 min), and at most
  ``_MAX_LIVE_CODES`` are live at once (oldest dropped).
- Failed attempts are rate-limited per (platform, sender). Past the
  limit every attempt gets the same generic reply without being checked,
  so a brute-forcer learns nothing.
- Success and failure replies don't reveal which part was wrong.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# No 0/O, 1/I/L — codes get read aloud and typed on phones.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
DEFAULT_TTL_S = 600
_MIN_TTL_S = 60
_MAX_TTL_S = 3600
_MAX_LIVE_CODES = 5

FAIL_LIMIT = 5
FAIL_WINDOW_S = 600

GENERIC_FAILURE = "That pairing code isn't valid or has expired."

# "/pair" everywhere; "!pair" too because Matrix commands use "!".
_PAIR_RE = re.compile(r"^\s*[/!]pair(?:@\S+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)

_lock = threading.Lock()
_failures: dict[tuple[str, str], list[float]] = {}


def _store_path() -> Path:
    override = os.environ.get("WINDY_OWNER_PAIRING_PATH")
    if override:
        return Path(os.path.expanduser(override))
    from windyfly.platform import windy_state_dir
    return windy_state_dir() / "owner-pairing.json"


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _load() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("owner-pairing store unreadable: %s", e)
        return []
    codes = raw.get("codes") if isinstance(raw, dict) else None
    return [c for c in (codes or []) if isinstance(c, dict)]


def _save(codes: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"codes": codes}, f, indent=2)
    tmp.replace(path)


def normalize_code(raw: str) -> str | None:
    """Canonical form of a user-typed code, or None if it can't be one."""
    cleaned = re.sub(r"[\s-]", "", raw or "").upper()
    if len(cleaned) != CODE_LEN or any(ch not in ALPHABET for ch in cleaned):
        return None
    return cleaned


def create_code(owner_identity: str, ttl_seconds: int = DEFAULT_TTL_S) -> dict[str, str]:
    """Mint a one-time pairing code for ``owner_identity`` (the hub ``sub``)."""
    owner_identity = (owner_identity or "").strip()
    if not owner_identity:
        raise ValueError("owner_identity is required")
    # The owner is the hub windy_identity_id, never an email: hub tokens
    # can carry an UNVERIFIED email for up to 24h, so an email proves
    # nothing about who the owner is.
    if "@" in owner_identity:
        raise ValueError("owner_identity must be the hub identity id, not an email")
    ttl = max(_MIN_TTL_S, min(_MAX_TTL_S, int(ttl_seconds or DEFAULT_TTL_S)))
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    now = time.time()
    expires = now + ttl
    with _lock:
        live = [c for c in _load() if float(c.get("expires_at", 0)) > now]
        live.append({"hash": _hash(code), "owner_identity": owner_identity,
                     "expires_at": expires})
        _save(live[-_MAX_LIVE_CODES:])
    logger.info("owner pairing code minted for identity %s (ttl %ds)", owner_identity, ttl)
    return {
        "code": f"{code[:4]}-{code[4:]}",
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
    }


def _consume(code: str) -> str | None:
    """Burn ``code`` if live; return its owner_identity."""
    want = _hash(code)
    now = time.time()
    with _lock:
        codes = _load()
        match = None
        keep = []
        for c in codes:
            if float(c.get("expires_at", 0)) <= now:
                continue
            if match is None and secrets.compare_digest(str(c.get("hash", "")), want):
                match = c
                continue
            keep.append(c)
        if match is not None or len(keep) != len(codes):
            _save(keep)
    return str(match["owner_identity"]) if match else None


def _rate_limited(key: tuple[str, str]) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < FAIL_WINDOW_S]
    _failures[key] = recent
    return len(recent) >= FAIL_LIMIT


def is_pair_command(text: str | None) -> bool:
    return bool(text) and _PAIR_RE.match(text or "") is not None


def loggable(text: str | None) -> str:
    """``text`` safe for a log line: a pairing message loses its code."""
    return "/pair <redacted>" if is_pair_command(text) else (text or "")


def try_pair(platform: str, sender_id: str | None, text: str | None) -> str | None:
    """Handle a ``/pair`` message. Returns the reply, or None if ``text``
    isn't a pairing command (the caller carries on as normal)."""
    m = _PAIR_RE.match(text or "")
    if not m:
        return None
    platform = (platform or "unknown").lower()
    sender = (str(sender_id).strip() if sender_id else "")
    if not sender:
        return GENERIC_FAILURE
    key = (platform, sender)
    with _lock:
        if _rate_limited(key):
            logger.warning("pairing attempts rate-limited for %s:%s", platform, sender)
            return GENERIC_FAILURE
    code = normalize_code(m.group(1) or "")
    owner_identity = _consume(code) if code else None
    if owner_identity is None:
        with _lock:
            _failures.setdefault(key, []).append(time.time())
        logger.info("pairing attempt failed for %s:%s", platform, sender)
        return GENERIC_FAILURE
    from windyfly.channels.identity import bind_owner
    bind_owner(platform, sender)
    with _lock:
        _failures.pop(key, None)
    logger.warning("owner paired: %s:%s is now the owner (hub identity %s)",
                   platform, sender, owner_identity)
    return f"Paired — you're now the owner on {platform}."


def _reset_for_tests() -> None:
    _failures.clear()
