"""The agent's lease ENCRYPTION key (strand gene B1.1; Vault plan C7, V7.6 c2c).

Vault leases are JWEs (ECDH-ES+A256KW, X25519, A256GCM) to a key the agent registers. It is a
SEPARATE key from the agent's ES256 signing key (never reuse a signing key for ECDH). The
private half lives in ``lease_keys.json`` (0600) in the same directory as credentials.json
(``WINDY_LEASE_KEYS_FILE`` overrides). A rotation keeps the previous key for a 24 h overlap so a
lease issued just before the rotation still decrypts.

A key's ``kid`` is Vault's own id for it, hex(sha256(utf8("wv-keyid|") || raw32)[:8]) (Vault
agent-routes.v1): Vault stamps that kid on every lease, so the agent must use the same one. The
registration body is exactly {"public_key": <standard padded base64 of the 32 raw bytes>}, never a
JWK. ``register`` takes the transport as a function: nothing here opens a connection, and the
route is still wired later, after Hub's gate (dark).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

logger = logging.getLogger(__name__)

OVERLAP_S = 24 * 3600
_KID_RE = re.compile(r"^[0-9a-f]{16}$")


class LeaseKeyRegistrationError(Exception):
    """Vault did not register the key as ours (wrong kid, bad reply, or a refusal). Nothing is stored."""


def vault_kid(pub_raw: bytes) -> str:
    """Vault's kid for a 32-byte X25519 public key."""
    if len(pub_raw) != 32:
        raise ValueError("an X25519 public key is 32 bytes")
    return hashlib.sha256(b"wv-keyid|" + pub_raw).digest()[:8].hex()


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
    """The stored keys. A missing file is a first run; a file that is there but cannot be read or
    is not a key list is logged (name and reason only, never its contents), then treated as empty,
    so the next key minted replaces it (old leases re-lease to the new key)."""
    try:
        d = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {"keys": []}
    except (OSError, ValueError) as e:
        logger.warning("lease key file %s cannot be read (%s): treating it as empty; "
                       "the next key minted replaces it", path, type(e).__name__)
        return {"keys": []}
    if not (isinstance(d, dict) and isinstance(d.get("keys"), list)):
        logger.warning("lease key file %s holds no key list: treating it as empty; "
                       "the next key minted replaces it", path)
        return {"keys": []}
    for k in d["keys"]:
        # A file written before Vault's kid (lk_<random>): the kid is a pure function of the key.
        try:
            k["kid"] = vault_kid(_unb64(k["public"]))
        except (KeyError, TypeError, ValueError):
            pass
    return d


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
    return {"kid": vault_kid(pub), "private": _b64(raw), "public": _b64(pub),
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


def registration_body(entry: dict[str, Any]) -> dict[str, str]:
    """The body of PUT /v1/agent/lease-key: the 32 raw public bytes, standard padded base64."""
    return {"public_key": base64.b64encode(_unb64(entry["public"])).decode("ascii")}


def register(put: Callable[[dict[str, str]], tuple[int, Any]], *, now: float | None = None,
             path: Path | None = None) -> str:
    """Register the active key with Vault through ``put(body) -> (status, json)``.

    Vault answers {"kid": <its 16-hex id>}. A non-2xx reply, a kid that is not 16 lowercase hex, or
    a kid that is not this key's own is refused loudly and nothing is stored (Windy Vault 10-12:
    a different kid means a wrong server, a proxy or a bug).
    """
    now = time.time() if now is None else now
    p = path or keys_path()
    active = ensure_active(now=now, path=p)
    status, reply = put(registration_body(active))
    if not 200 <= int(status) < 300:
        raise LeaseKeyRegistrationError(f"Vault refused the lease key (HTTP {status})")
    kid = reply.get("kid") if isinstance(reply, dict) else None
    if not isinstance(kid, str) or not _KID_RE.match(kid):
        raise LeaseKeyRegistrationError("Vault answered without a valid kid")
    if kid != active["kid"]:
        raise LeaseKeyRegistrationError("Vault's kid is not this key's kid")
    data = _load(p)
    for k in data["keys"]:
        if k["kid"] == kid:
            k["registered_at"] = now
    _save(p, data)
    return kid


def public_jwk(entry: dict[str, Any]) -> dict[str, str]:
    """The public half as a JWK (local use only; Vault takes ``registration_body``): no private material."""
    return {"kty": "OKP", "crv": "X25519", "x": entry["public"], "kid": entry["kid"], "use": "enc"}


def private_for(kid: str, *, now: float | None = None, path: Path | None = None) -> X25519PrivateKey | None:
    """The private key for ``kid`` while it is active or inside its overlap, else None
    (an unknown kid means: ask for a new lease)."""
    now = time.time() if now is None else now
    for k in _load(path or keys_path())["keys"]:
        if k["kid"] == kid and (k["status"] == "active" or (k.get("retire_after") or 0) > now):
            return X25519PrivateKey.from_private_bytes(_unb64(k["private"]))
    return None
