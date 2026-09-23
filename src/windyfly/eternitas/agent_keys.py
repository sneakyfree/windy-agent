"""Agent signing keys (Eternitas agent-keys v1).

A passport number proves a passport exists, not that the caller holds it. v1
gives every hatched agent its own ES256 (P-256) key pair:

* the PRIVATE key is generated here, on the device the agent runs on, and never
  leaves it — not to Eternitas, not to logs, not to cloud backups (the cloud
  backup ships only the SQLite database);
* Eternitas stores only the PUBLIC JWK, so anyone can verify what the agent
  signs (Windy Drops signed publish, #35) against
  ``GET /api/v1/bots/{passport}/keys``.

Spec: windy-orchestra ``specs/eternitas-agent-keys.v1.md`` (approved
2026-09-23). This module is windy-agent's side (spec rollout step 2).

Storage — ONE file per agent, mode 0600, atomic writes with a timestamped
backup (the same discipline as ``ept_refresh``)::

    WINDY_CREDENTIALS_FILE   explicit path (multi-agent machines, services)
    else  <state dir>/credentials.json   (``WINDY_STATE_DIR`` or ~/.windy)

Schema (other top-level keys in the file are preserved)::

    {"eternitas": {
        "private_key": "<PEM of the ACTIVE key>",   # the Drops SDK reads this
        "kid": "<RFC 7638 thumbprint of the active key>",
        "keys": [{"kid", "private_key", "status": "active|retiring",
                  "created_at", "registered_at"}]}}

At most two keys live in the file: the active one and, during a rotation, the
``retiring`` one until Eternitas confirms the retire.

Nothing here raises into its callers: every public entry point returns a
result dict with a ``status``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

logger = logging.getLogger(__name__)

ALG = "ES256"
CRV = "P-256"
ARTIFACT_TYP = "eternitas-sig+jws"   # spec §3
PLATFORM_POP_TYP = "eternitas-pop+jwt"  # spec §2 mode A
DPOP_TYP = "dpop+jwt"  # RFC 9449
_HTTP_TIMEOUT = 20.0
_UNSUPPORTED_LOGGED = False  # "not yet supported by Eternitas" is logged once per process


# ── base64url + JWK ──────────────────────────────────────────────────

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("not a P-256 private key")
    return key


def public_jwk(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> dict[str, str]:
    """The minimal public JWK (the RFC 7638 required members only)."""
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    nums = pub.public_numbers()
    return {
        "kty": "EC",
        "crv": CRV,
        "x": _b64u(nums.x.to_bytes(32, "big")),
        "y": _b64u(nums.y.to_bytes(32, "big")),
    }


def thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint: SHA-256 over the required members, sorted, no whitespace."""
    if jwk.get("kty") == "EC":
        required = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    elif jwk.get("kty") == "RSA":  # only for the RFC 7638 test vector
        required = {k: jwk[k] for k in ("e", "kty", "n")}
    else:
        raise ValueError(f"unsupported kty {jwk.get('kty')!r}")
    canonical = json.dumps(required, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64u(hashlib.sha256(canonical).digest())


def published_jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    """The JWK as registered: required members + kid/alg/use (spec §3 guarantees)."""
    jwk = public_jwk(key)
    return {**jwk, "kid": thumbprint(jwk), "alg": ALG, "use": "sig"}


# ── JWS (compact, ES256) ─────────────────────────────────────────────

def _sign(key: ec.EllipticCurvePrivateKey, signing_input: bytes) -> str:
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _compact(key: ec.EllipticCurvePrivateKey, header: dict[str, Any], payload: bytes,
             *, detached: bool = False) -> str:
    h = _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64u(payload)
    sig = _sign(key, f"{h}.{p}".encode("ascii"))
    return f"{h}..{sig}" if detached else f"{h}.{p}.{sig}"


def verify_jws(jws: str, jwk: dict[str, Any], detached_payload: bytes | None = None) -> dict[str, Any] | None:
    """Verify a compact (or detached) ES256 JWS against a public JWK.

    Returns ``{"header": …, "payload": bytes}`` or None. Used by the tests and
    by ``windy agent-key status``; real verifiers use Eternitas's SDK."""
    try:
        h, p, s = jws.split(".")
        if detached_payload is not None:
            if p:
                return None
            p = _b64u(detached_payload)
        header = json.loads(_b64u_decode(h))
        if header.get("alg") != ALG:
            return None
        raw = _b64u_decode(s)
        if len(raw) != 64:
            return None
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pub = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64u_decode(jwk["x"]), "big"),
            int.from_bytes(_b64u_decode(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        pub.verify(der, f"{h}.{p}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return {"header": header, "payload": _b64u_decode(p)}
    except Exception:
        return None


def registration_proof(key: ec.EllipticCurvePrivateKey, passport: str, nonce: str,
                       iat: int | None = None) -> str:
    """Proof of possession for POST /bots/{p}/keys: a JWS by the NEW key over
    ``{passport, nonce, iat}`` (spec §1). kid AND passport ride in the protected
    header too, matching the artifact rule (§3 decision)."""
    kid = thumbprint(public_jwk(key))
    iat = int(time.time()) if iat is None else iat
    header = {"alg": ALG, "typ": "JWT", "kid": kid, "passport": passport}
    payload = json.dumps({"passport": passport, "nonce": nonce, "iat": iat},
                         separators=(",", ":")).encode("utf-8")
    return _compact(key, header, payload)


def platform_pop(key: ec.EllipticCurvePrivateKey, passport: str, aud: str, nonce: str,
                 iat: int | None = None) -> str:
    """Mode A challenge-response proof for a platform (spec §2 A)."""
    iat = int(time.time()) if iat is None else iat
    header = {"alg": ALG, "typ": PLATFORM_POP_TYP, "kid": thumbprint(public_jwk(key))}
    claims = {"iss": passport, "aud": aud, "nonce": nonce, "iat": iat, "exp": iat + 60}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


def dpop_proof(key: ec.EllipticCurvePrivateKey, htm: str, htu: str,
               iat: int | None = None, jti: str | None = None) -> str:
    """RFC 9449 DPoP proof. A helper only; mode B token exchange is Eternitas step 4."""
    iat = int(time.time()) if iat is None else iat
    header = {"typ": DPOP_TYP, "alg": ALG, "jwk": public_jwk(key)}
    claims = {"jti": jti or str(uuid.uuid4()), "htm": htm.upper(), "htu": htu, "iat": iat}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


# ── credentials file ─────────────────────────────────────────────────

def credentials_path() -> Path:
    explicit = os.environ.get("WINDY_CREDENTIALS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from windyfly.platform import windy_state_dir
    return windy_state_dir() / "credentials.json"


def load_credentials(path: Path | None = None) -> dict[str, Any]:
    path = path or credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_credentials(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomic 0600 write; the previous file (if any) is kept as a timestamped
    0600 backup next to it. Private keys never leave this directory."""
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
            n += 1
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _section(data: dict[str, Any]) -> dict[str, Any]:
    sec = data.get("eternitas")
    if not isinstance(sec, dict):
        sec = {}
        data["eternitas"] = sec
    if not isinstance(sec.get("keys"), list):
        sec["keys"] = []
    return sec


def _set_active(sec: dict[str, Any], entry: dict[str, Any]) -> None:
    sec["private_key"] = entry["private_key"]
    sec["kid"] = entry["kid"]


def active_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    sec = data.get("eternitas") or {}
    kid = sec.get("kid")
    for e in sec.get("keys") or []:
        if e.get("kid") == kid and e.get("status") == "active":
            return e
    return None


def _new_entry(key: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    return {
        "kid": thumbprint(public_jwk(key)),
        "private_key": private_key_pem(key),
        "status": "active",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "registered_at": None,
    }


# ── identity + auth ──────────────────────────────────────────────────

def _claims(token: str) -> dict[str, Any]:
    try:
        return json.loads(_b64u_decode(token.split(".")[1]))
    except Exception:
        return {}


def current_passport() -> str:
    env = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if env:
        return env
    return str(_claims(os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")).get("sub") or "")


def _own_ept(passport: str) -> str:
    token = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
    c = _claims(token)
    exp = c.get("exp")
    if token and c.get("sub") == passport and isinstance(exp, (int, float)) and exp > time.time():
        return token
    return ""


def _base_url() -> str:
    from windyfly.eternitas.url import resolve_eternitas_url
    return resolve_eternitas_url("https://api.eternitas.ai")


def stored_hub_token() -> str:
    """The owner's saved hub session token (``windy login``), refreshed if due.

    Good enough for ``revoke`` (spec: owner via hub login). NOT for owner key
    registration: that needs a fresh credential entry, see ``fresh_owner_token``."""
    try:
        from windyfly import hub_login
        return hub_login.get_access_token() or ""
    except Exception:
        return ""


OWNER_REAUTH_MAX_AGE_S = 600  # Eternitas: auth_time within 10 minutes


def fresh_owner_token(*, open_browser: bool = True, echo: Any = print) -> str:
    """A brand-new hub token from a browser sign-in with ``prompt=login``.

    Owner recovery registration (``reset``) requires ``email_verified`` and an
    ``auth_time`` under 10 minutes old. A stored or refreshed token never
    qualifies (refresh omits auth_time), so this always runs a new PKCE login
    and never reads or overwrites the saved session."""
    from windyfly import hub_login
    who = hub_login.login(open_browser=open_browser, echo=echo, reauth=True, store=False)
    token = str(who.get("access_token") or "")
    c = _claims(token)
    auth_time = c.get("auth_time")
    if c.get("email_verified") is not True:
        logger.warning("agent keys: the Windy sign-in says this email isn't verified; "
                       "Eternitas will refuse the reset")
    if not isinstance(auth_time, (int, float)) or time.time() - auth_time > OWNER_REAUTH_MAX_AGE_S:
        logger.warning("agent keys: the Windy sign-in carried no fresh auth_time; "
                       "Eternitas may refuse the reset")
    return token


# ── Eternitas calls ──────────────────────────────────────────────────

class _Unsupported(Exception):
    """The agent-keys routes aren't deployed on this Eternitas yet."""


def _is_route_missing(resp: httpx.Response) -> bool:
    # FastAPI's catch-all 404 says exactly "Not Found"; an unknown passport on a
    # deployed route comes back with a specific detail (spec §3: 404, never 200).
    if resp.status_code != 404:
        return False
    try:
        return resp.json().get("detail") == "Not Found"
    except Exception:
        return True


def _post(client: httpx.Client, path: str, bearer: str, body: dict[str, Any] | None = None) -> httpx.Response:
    resp = client.post(f"{_base_url()}{path}", json=body or {},
                       headers={"Authorization": f"Bearer {bearer}"})
    if _is_route_missing(resp):
        raise _Unsupported(path)
    return resp


def _register(client: httpx.Client, passport: str, key: ec.EllipticCurvePrivateKey, bearer: str) -> httpx.Response:
    ch = _post(client, f"/api/v1/bots/{passport}/keys/challenge", bearer)
    if ch.status_code not in (200, 201):
        return ch
    nonce = str(ch.json().get("nonce") or "")
    if not nonce:
        raise ValueError("challenge returned no nonce")
    return _post(client, f"/api/v1/bots/{passport}/keys", bearer, {
        "jwk": published_jwk(key),
        "proof": registration_proof(key, passport, nonce),
    })


def _note_unsupported() -> dict[str, Any]:
    global _UNSUPPORTED_LOGGED
    if not _UNSUPPORTED_LOGGED:
        logger.info("agent keys not yet supported by Eternitas; will retry later")
        _UNSUPPORTED_LOGGED = True
    else:
        logger.debug("agent keys not yet supported by Eternitas")
    return {"status": "unsupported"}


def _http_fail(what: str, resp: httpx.Response) -> dict[str, Any]:
    detail = ""
    try:
        detail = str(resp.json().get("detail", ""))[:120]
    except Exception:
        pass
    logger.warning("agent keys: %s refused (HTTP %s %s)", what, resp.status_code, detail)
    return {"status": "failed", "http": resp.status_code, "detail": detail}


# ── public operations ────────────────────────────────────────────────

_LOCK = threading.Lock()


def ensure_registered(*, transport: httpx.BaseTransport | None = None,
                      path: Path | None = None) -> dict[str, Any]:
    """Make sure this agent has an active key registered at Eternitas.

    Generates a key if there is none (persisted BEFORE it's registered, so a
    failed registration never loses it), then registers it with the agent's
    own EPT, falling back to the owner's hub sign-in. Also finishes a pending
    rotation (retires a ``retiring`` key). Never raises.

    ``status``: registered | already_registered | unsupported | no_passport |
    needs_login | failed.
    """
    try:
        with _LOCK:
            return _ensure(transport, path or credentials_path())
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: unexpected %s", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def _bearer(passport: str) -> tuple[str, str]:
    """Bearer for the agent's own key operations: its current EPT. The owner's
    stored hub session can't register keys (Eternitas wants a fresh sign-in),
    so there is no silent owner fallback; that's what ``reset`` is for."""
    own = _own_ept(passport)
    return (own, "own_ept") if own else ("", "")


def _ensure(transport, path: Path) -> dict[str, Any]:
    passport = current_passport()
    if not passport:
        return {"status": "no_passport"}
    data = load_credentials(path)
    sec = _section(data)
    entry = active_entry(data)
    if entry is None:
        key = generate_private_key()
        entry = _new_entry(key)
        sec["keys"] = [e for e in sec["keys"] if e.get("status") == "retiring"] + [entry]
        _set_active(sec, entry)
        save_credentials(data, path)
        logger.info("agent keys: generated a new signing key %s…", entry["kid"][:8])

    if entry.get("registered_at") and not any(e.get("status") == "retiring" for e in sec["keys"]):
        return {"status": "already_registered", "kid": entry["kid"]}

    bearer, via = _bearer(passport)
    if not bearer:
        logger.warning("agent keys: can't register (the passport token is missing or expired); "
                       "run `windy login` then `windy ept refresh`, or `windy agent-key reset`")
        return {"status": "needs_login", "kid": entry["kid"]}

    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
        if not entry.get("registered_at"):
            resp = _register(client, passport, load_private_key(entry["private_key"]), bearer)
            if resp.status_code not in (200, 201):
                return _http_fail("key registration", resp)
            entry["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_credentials(data, path)
            logger.info("agent keys: registered %s… for %s (via %s)", entry["kid"][:8], passport[:9], via)
        _retire_pending(client, passport, bearer, data, path)
    return {"status": "registered", "kid": entry["kid"], "via": via}


def _retire_pending(client: httpx.Client, passport: str, bearer: str,
                    data: dict[str, Any], path: Path) -> None:
    sec = _section(data)
    for e in [e for e in sec["keys"] if e.get("status") == "retiring"]:
        resp = _post(client, f"/api/v1/bots/{passport}/keys/{e['kid']}/retire", bearer)
        if resp.status_code in (200, 204, 404, 409):  # 404/409: already gone/retired
            sec["keys"] = [k for k in sec["keys"] if k.get("kid") != e["kid"]]
            save_credentials(data, path)
            logger.info("agent keys: retired %s…", e["kid"][:8])
        else:
            _http_fail("key retire", resp)


def rotate(*, transport: httpx.BaseTransport | None = None, path: Path | None = None) -> dict[str, Any]:
    """Register a fresh key, make it active, then retire the old one.

    If the new key can't be registered it's dropped and the old key stays
    active. If the retire fails, the old key stays in the file as ``retiring``
    and the daily job retries."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = current_passport()
            if not passport:
                return {"status": "no_passport"}
            data = load_credentials(path)
            sec = _section(data)
            old = active_entry(data)
            if old is None or not old.get("registered_at"):
                return {"status": "failed", "error": "no_registered_key"}
            bearer, via = _bearer(passport)
            if not bearer:
                return {"status": "needs_login"}
            key = generate_private_key()
            new = _new_entry(key)
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _register(client, passport, key, bearer)
                if resp.status_code not in (200, 201):
                    return _http_fail("new key registration", resp)
                new["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                old["status"] = "retiring"
                sec["keys"] = [old, new]
                _set_active(sec, new)
                save_credentials(data, path)
                _retire_pending(client, passport, bearer, data, path)
            return {"status": "rotated", "kid": new["kid"], "old_kid": old["kid"], "via": via}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: rotate failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def reset(*, transport: httpx.BaseTransport | None = None, path: Path | None = None,
          reason: str = "reset by owner", owner_token: Any = None) -> dict[str, Any]:
    """Owner recovery for a lost or compromised key.

    1. A FRESH owner sign-in (``prompt=login``; ``owner_token`` is the
       callable that produces it, default ``fresh_owner_token``) — never the
       stored session.
    2. Register a brand-new key with that hub token + PoP. Eternitas flags it
       ``registered_via: owner_recovery``, notifies platforms, emails the owner
       and allows this once per 24h per passport (429 → ``rate_limited``).
    3. Revoke every other kid Eternitas lists as active (the public JWKS, so a
       key lost with an old device is caught too) plus any in the local file.

    Registering before revoking means a refused reset never leaves the agent
    with no key at all."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = current_passport()
            if not passport:
                return {"status": "no_passport"}
            get_token = owner_token or fresh_owner_token
            try:
                hub = str(get_token() or "")
            except Exception as exc:
                logger.warning("agent keys: owner sign-in failed (%s)", type(exc).__name__)
                return {"status": "needs_login", "error": str(exc)[:200]}
            if not hub:
                return {"status": "needs_login"}
            data = load_credentials(path)
            sec = _section(data)
            local_kids = {str(e["kid"]) for e in sec["keys"] if e.get("kid")}
            key = generate_private_key()
            new = _new_entry(key)
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _register(client, passport, key, hub)
                if resp.status_code == 429:
                    logger.warning("agent keys: reset refused, once per 24h per passport")
                    return {"status": "rate_limited"}
                if resp.status_code not in (200, 201):
                    return _http_fail("owner key registration", resp)
                new["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                sec["keys"] = [new]
                _set_active(sec, new)
                save_credentials(data, path)

                old = set(local_kids)
                try:
                    listed = client.get(f"{_base_url()}/api/v1/bots/{passport}/keys")
                    if listed.status_code == 200:
                        old |= {str(k.get("kid")) for k in listed.json().get("keys", [])
                                if k.get("status") in ("active", None) and k.get("kid")}
                except Exception:
                    pass
                old.discard(new["kid"])
                revoked: list[str] = []
                failed: list[str] = []
                for kid in sorted(old):
                    r = _post(client, f"/api/v1/bots/{passport}/keys/{kid}/revoke", hub, {"reason": reason})
                    (revoked if r.status_code in (200, 204, 404, 409) else failed).append(kid)
            if failed:
                logger.warning("agent keys: reset could not revoke %d old key(s); run "
                               "`windy agent-key revoke --kid …`", len(failed))
            return {"status": "reset", "kid": new["kid"], "revoked": revoked, "revoke_failed": failed}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: reset failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def revoke(kid: str, *, reason: str = "revoked by owner",
           transport: httpx.BaseTransport | None = None, path: Path | None = None) -> dict[str, Any]:
    """Revoke one kid (agent's own EPT, else the owner). The key is removed from
    the file; if it was the active key, the next boot generates a fresh one."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = current_passport()
            if not passport:
                return {"status": "no_passport"}
            bearer, via = _bearer(passport)
            if not bearer:
                bearer, via = stored_hub_token(), "owner_hub_login"
            if not bearer:
                return {"status": "needs_login"}
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _post(client, f"/api/v1/bots/{passport}/keys/{kid}/revoke", bearer, {"reason": reason})
            if resp.status_code not in (200, 204):
                return _http_fail("key revoke", resp)
            data = load_credentials(path)
            sec = _section(data)
            was_active = sec.get("kid") == kid
            sec["keys"] = [e for e in sec["keys"] if e.get("kid") != kid]
            if was_active:
                sec.pop("private_key", None)
                sec.pop("kid", None)
            save_credentials(data, path)
            return {"status": "revoked", "kid": kid, "was_active": was_active, "via": via}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: revoke failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def sign_artifact(payload: bytes, *, path: Path | None = None,
                  signed_at: str | None = None) -> str:
    """Detached JWS over ``payload`` with the ACTIVE key (spec §3).

    Protected header: ``{alg, typ: eternitas-sig+jws, kid, passport, signed_at}``.
    kid and passport are both required and bound by the signature. Raises
    ``RuntimeError`` if there is no active key or no passport; callers decide
    how to surface that."""
    passport = current_passport()
    if not passport:
        raise RuntimeError("no passport on this agent")
    entry = active_entry(load_credentials(path or credentials_path()))
    if entry is None:
        raise RuntimeError("no active signing key (run `windy agent-key status`)")
    key = load_private_key(entry["private_key"])
    header = {
        "alg": ALG,
        "typ": ARTIFACT_TYP,
        "kid": entry["kid"],
        "passport": passport,
        "signed_at": signed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return _compact(key, header, payload, detached=True)


def status(*, transport: httpx.BaseTransport | None = None,
           path: Path | None = None, check_remote: bool = True) -> dict[str, Any]:
    """Local view (+ the public JWKS when reachable). Never includes key material."""
    data = load_credentials(path or credentials_path())
    sec = data.get("eternitas") or {}
    keys = [{k: e.get(k) for k in ("kid", "status", "created_at", "registered_at")}
            for e in sec.get("keys") or []]
    out: dict[str, Any] = {"passport": current_passport(), "active_kid": sec.get("kid"),
                           "keys": keys, "remote": None}
    if check_remote and out["passport"]:
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = client.get(f"{_base_url()}/api/v1/bots/{out['passport']}/keys")
            if _is_route_missing(resp):
                out["remote"] = "unsupported"
            elif resp.status_code == 200:
                out["remote"] = {k.get("kid"): k.get("status") for k in resp.json().get("keys", [])}
            else:
                out["remote"] = f"http {resp.status_code}"
        except Exception as exc:
            out["remote"] = f"unreachable ({type(exc).__name__})"
    return out


# ── background / hooks ───────────────────────────────────────────────

def disabled() -> bool:
    """Automatic key work is off under pytest and with WINDY_DISABLE_AGENT_KEYS
    (explicit `windy agent-key …` commands still run)."""
    return bool(os.environ.get("WINDY_DISABLE_AGENT_KEYS") or os.environ.get("PYTEST_CURRENT_TEST"))


def ensure_in_background() -> threading.Thread | None:
    """Boot: fire-and-forget. Skipped under pytest and WINDY_DISABLE_AGENT_KEYS."""
    if disabled():
        return None
    t = threading.Thread(target=ensure_registered, name="agent-keys", daemon=True)
    t.start()
    return t


def register_after_signin() -> dict[str, Any]:
    """Hook for `windy login` and a completed hatch: a synchronous best-effort
    ensure_registered (the CLI process may exit right after). Never raises."""
    if disabled():
        return {"status": "skipped"}
    return ensure_registered()
