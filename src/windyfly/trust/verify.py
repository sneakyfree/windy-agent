"""Verify trust.changed webhook signatures from Eternitas.

Contract (per /Users/thewindstorm/eternitas/docs/webhooks.md):
  - `X-Eternitas-Signature`: HMAC-SHA256 of the raw body, shared
    secret between Eternitas and the receiver (env var
    ETERNITAS_WEBHOOK_SECRET).
  - `X-Windy-Signature`: detached ES256 JWS over the raw body, signed
    with Eternitas's private key. Verified against the JWKS at
    `${ETERNITAS_URL}/.well-known/eternitas-keys`, cached locally
    for 24 h.

Either signature passing by itself is NOT enough. Both must verify —
the HMAC catches replay by someone who captured a body, the JWS
catches replay by someone who captured the shared HMAC secret.

Never-raises policy: on any verification failure we return False +
a reason. The caller (the webhook route) is responsible for
returning the 401. That way the route handler can log uniformly
and never leak which check failed to the attacker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_JWKS_CACHE_FILE = PROJECT_ROOT / "data" / "eternitas_jwks.json"
_JWKS_TTL_SECONDS = 24 * 3600


@dataclass
class VerifyResult:
    ok: bool
    reason: str = ""


def _b64url_decode(s: str) -> bytes:
    """Decode base64url (JOSE) — adds padding, replaces chars."""
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s.encode("ascii"))


# ----------------------------------------------------------------------
# HMAC
# ----------------------------------------------------------------------


def verify_hmac(body: bytes, signature_header: str, secret: str) -> VerifyResult:
    """Verify the X-Eternitas-Signature HMAC-SHA256.

    Expected header format:
        sha256=<hex>           or
        t=<unix>,v1=<hex>      (Stripe-style timestamp + sig, tolerated)
    """
    if not secret:
        return VerifyResult(False, "HMAC secret not configured")
    if not signature_header:
        return VerifyResult(False, "missing X-Eternitas-Signature header")

    # Accept either "sha256=HEX" or "v1=HEX" (with an optional t= prefix).
    sig_hex = ""
    for part in signature_header.split(","):
        part = part.strip()
        for prefix in ("sha256=", "v1="):
            if part.startswith(prefix):
                sig_hex = part[len(prefix):]
                break
        if sig_hex:
            break

    if not sig_hex:
        return VerifyResult(False, "X-Eternitas-Signature format not recognised")

    try:
        provided = bytes.fromhex(sig_hex)
    except ValueError:
        return VerifyResult(False, "X-Eternitas-Signature not valid hex")

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, provided):
        return VerifyResult(False, "HMAC mismatch")
    return VerifyResult(True)


# ----------------------------------------------------------------------
# JWKS + JWS
# ----------------------------------------------------------------------


def _load_jwks_cache() -> dict | None:
    if not _JWKS_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_JWKS_CACHE_FILE.read_text())
        if time.time() - data.get("cached_at", 0) > _JWKS_TTL_SECONDS:
            return None
        return data.get("jwks")
    except (json.JSONDecodeError, OSError):
        return None


def _save_jwks_cache(jwks: dict) -> None:
    _JWKS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JWKS_CACHE_FILE.write_text(
        json.dumps({"jwks": jwks, "cached_at": time.time()}, indent=2)
    )


def fetch_jwks(eternitas_url: str, force_refresh: bool = False) -> dict | None:
    """Return the JWKS, cached for 24 h on disk."""
    if not force_refresh:
        cached = _load_jwks_cache()
        if cached:
            return cached

    if not eternitas_url:
        return None
    try:
        resp = httpx.get(
            f"{eternitas_url.rstrip('/')}/.well-known/eternitas-keys",
            timeout=5.0,
        )
        resp.raise_for_status()
        jwks = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        logger.warning("JWKS fetch failed: %s", exc)
        return None

    _save_jwks_cache(jwks)
    return jwks


def _ec_public_key_from_jwk(jwk: dict) -> ec.EllipticCurvePublicKey | None:
    """Build an ES256 public key from a JWK's x, y coordinates."""
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return None
    try:
        x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
    except (KeyError, ValueError):
        return None
    pub_numbers = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1())
    return pub_numbers.public_key()


def verify_jws(body: bytes, signature_header: str, jwks: dict) -> VerifyResult:
    """Verify a detached ES256 JWS.

    Expected header shape (detached JWS, RFC 7797 style):
        <protected_header_b64>..<signature_b64>
    The payload is the raw body (detached) — NOT a base64-encoded
    JWS payload segment.

    Returns ok + empty reason on success.
    """
    if not signature_header:
        return VerifyResult(False, "missing X-Windy-Signature header")
    parts = signature_header.split(".")
    if len(parts) != 3:
        return VerifyResult(False, "X-Windy-Signature not a 3-part JWS")
    protected_b64, payload_b64, sig_b64 = parts
    if payload_b64 not in ("", ""):
        # Detached JWS: middle segment must be empty.
        return VerifyResult(False, "detached JWS requires empty payload segment")

    try:
        header_json = _b64url_decode(protected_b64)
        header = json.loads(header_json)
    except (ValueError, json.JSONDecodeError):
        return VerifyResult(False, "JWS protected header not valid JSON")

    if header.get("alg") != "ES256":
        return VerifyResult(False, f"alg {header.get('alg')!r} not supported")
    kid = header.get("kid", "")

    # Pick the JWK whose kid matches; fall back to any EC key.
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not keys:
        return VerifyResult(False, "JWKS has no keys")
    candidate = None
    for jwk in keys:
        if kid and jwk.get("kid") == kid:
            candidate = jwk
            break
    if not candidate:
        # No kid match — be strict and refuse rather than guessing.
        return VerifyResult(False, f"no JWK with kid {kid!r}")

    pub = _ec_public_key_from_jwk(candidate)
    if pub is None:
        return VerifyResult(False, "JWK not a P-256 EC key")

    try:
        raw_sig = _b64url_decode(sig_b64)
    except ValueError:
        return VerifyResult(False, "JWS signature segment not valid base64url")
    if len(raw_sig) != 64:
        return VerifyResult(False, f"ES256 signature must be 64 bytes, got {len(raw_sig)}")

    # JWS ES256 uses raw R||S concat; cryptography wants DER.
    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    der_sig = encode_dss_signature(r, s)

    # Detached JWS signing input: protected_b64 + "." + b64url(body)
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    signing_input = (protected_b64 + "." + body_b64).encode("ascii")

    try:
        pub.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return VerifyResult(False, "JWS signature does not verify")
    except Exception as exc:
        return VerifyResult(False, f"JWS verify error: {exc}")

    return VerifyResult(True)


# ----------------------------------------------------------------------
# Top-level
# ----------------------------------------------------------------------


def verify_webhook(
    body: bytes,
    headers: dict[str, str],
    *,
    hmac_secret: str | None = None,
    eternitas_url: str | None = None,
) -> VerifyResult:
    """Verify both signatures on an inbound trust.changed webhook.

    Strict mode: both signatures must verify. When neither is
    configured AND WINDYFLY_TRUST_STRICT is unset, we fail open with
    a warning — preserves the dev-mode ergonomic, same as the trust
    gate.
    """
    strict = os.environ.get("WINDYFLY_TRUST_STRICT", "").lower() in ("1", "true", "yes")
    hmac_secret = hmac_secret if hmac_secret is not None else os.environ.get("ETERNITAS_WEBHOOK_SECRET", "")
    if eternitas_url is None:
        from windyfly.eternitas.url import resolve_eternitas_url
        eternitas_url = resolve_eternitas_url()

    hmac_header = _header(headers, "X-Eternitas-Signature")
    jws_header = _header(headers, "X-Windy-Signature")

    # Dev short-circuit: nothing configured AND no signatures offered.
    if not hmac_header and not jws_header and not hmac_secret and not eternitas_url:
        if strict:
            return VerifyResult(False, "no signatures and strict mode is on")
        logger.warning("trust webhook: no signature verification (dev fail-open)")
        return VerifyResult(True, "dev fail-open")

    hmac_ok = verify_hmac(body, hmac_header or "", hmac_secret)
    if not hmac_ok.ok:
        logger.warning("trust webhook HMAC failed: %s", hmac_ok.reason)
        return hmac_ok

    jwks = fetch_jwks(eternitas_url or "")
    if jwks is None:
        return VerifyResult(False, "JWKS unavailable")
    jws_ok = verify_jws(body, jws_header or "", jwks)
    if not jws_ok.ok:
        logger.warning("trust webhook JWS failed: %s", jws_ok.reason)
        return jws_ok

    return VerifyResult(True)


def _header(headers: dict[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    lower = name.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return ""
