"""The agent's lease ENCRYPTION key (strand gene B1.1; Vault plan C7, V7.6 c2c).

Vault leases are JWEs (ECDH-ES+A256KW, X25519, A256GCM) to a key the agent registers. It is a
SEPARATE key from the agent's ES256 signing key (never reuse a signing key for ECDH). The
private half lives in ``lease_keys.json`` (0600) in the same directory as credentials.json
(``WINDY_LEASE_KEYS_FILE`` overrides). A rotation keeps the previous key for a 24 h overlap so a
lease issued just before the rotation still decrypts. Nothing here talks to the Vault: the
registration route (``PUT /v1/agent/lease-key``) is wired later, after Hub's gate.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

OVERLAP_S = 24 * 3600


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keys_path() -> Path:
    explicit = os.environ.get("WINDY_LEASE_KEYS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from windyfly.eternitas.agent_keys import credentials_path

    return credentials_path().with_name("lease_keys.json")


def _load(path: Path) -> dict[str, Any]:
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("keys"), list) else {"keys": []}
    except (OSError, ValueError):
        return {"keys": []}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".lease_keys.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _new_entry(now: float) -> dict[str, Any]:
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kid": "lk_" + secrets.token_hex(6), "private": _b64(raw), "public": _b64(pub),
            "created_at": now, "status": "active", "retire_after": None}


def prune(data: dict[str, Any], now: float) -> bool:
    keep = [k for k in data["keys"]
            if k["status"] == "active" or (k.get("retire_after") or 0) > now]
    changed = len(keep) != len(data["keys"])
    data["keys"] = keep
    return changed


def ensure_active(*, now: float | None = None, path: Path | None = None) -> dict[str, Any]:
    """The active key entry (created on first use). Also drops retired keys past their overlap."""
    now = time.time() if now is None else now
    p = path or keys_path()
    data = _load(p)
    changed = prune(data, now)
    active = next((k for k in data["keys"] if k["status"] == "active"), None)
    if active is None:
        active = _new_entry(now)
        data["keys"].append(active)
        changed = True
    if changed:
        _save(p, data)
    return active


def rotate(*, now: float | None = None, path: Path | None = None) -> dict[str, Any]:
    """New active key; the previous one is honored for OVERLAP_S so in-flight leases decrypt."""
    now = time.time() if now is None else now
    p = path or keys_path()
    data = _load(p)
    for k in data["keys"]:
        if k["status"] == "active":
            k["status"], k["retire_after"] = "retiring", now + OVERLAP_S
    prune(data, now)
    new = _new_entry(now)
    data["keys"].append(new)
    _save(p, data)
    return new


def public_jwk(entry: dict[str, Any]) -> dict[str, str]:
    """The public half as a JWK for registration: contains no private material."""
    return {"kty": "OKP", "crv": "X25519", "x": entry["public"], "kid": entry["kid"], "use": "enc"}


def private_for(kid: str, *, now: float | None = None, path: Path | None = None) -> X25519PrivateKey | None:
    """The private key for ``kid`` while it is active or inside its overlap, else None
    (an unknown kid means: ask for a new lease)."""
    now = time.time() if now is None else now
    for k in _load(path or keys_path())["keys"]:
        if k["kid"] == kid and (k["status"] == "active" or (k.get("retire_after") or 0) > now):
            return X25519PrivateKey.from_private_bytes(_unb64(k["private"]))
    return None
