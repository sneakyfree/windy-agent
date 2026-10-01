"""verify.py: consumer-side verification of a signed drop (reference loader for surfaces such as Windy Fly).

Pure and offline: the caller fetches the inputs (the passport's keys from Eternitas, the revocation lists, the
bundle bytes) and this answers ok or one stable reason code. It mirrors the registry's checks, in the same
order, so a surface refuses exactly what the registry would refuse (windy-registry services/signature_verify.py;
conformance vectors: tools/conformance/jws/vectors.json).

What a surface MUST still do itself: fetch the keys and the revocation/suspension feeds (and fail closed when
they are unreachable), refuse an unsigned drop by default, enforce its own minimum integrity band (see
`check_min_band`), and ask the owner before installing. `verify_signed_drop` decides authenticity only, never trust.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .canonical import canonicalize

JWS_TYP = "eternitas-sig+jws"

# Stable reason codes (the conformance vectors pin these; do not rename).
REASONS = (
    "ok", "bundle_sha_mismatch", "no_signature", "bad_algorithm", "no_passport", "no_jws", "digest_mismatch",
    "bad_jws_format", "bad_header", "passport_mismatch", "bad_sig_length", "passport_revoked", "key_revoked",
    "passport_suspended", "unknown_passport", "unknown_key", "bad_key_type", "kid_mismatch", "key_not_active",
    "bad_signature",
)


# Integrity bands (Eternitas), ascending. `unproven` is OFF the scale: a neutral standing every newly hatched
# agent carries, not a penalty and not comparable. Whether a surface accepts it is an explicit choice.
BAND_ORDER = ("critical", "poor", "fair", "good", "exceptional")
BAND_REASONS = ("ok", "band_below_minimum", "band_unproven", "band_unknown")


def check_min_band(band: str | None, minimum: str | None, *, allow_unproven: bool = False) -> tuple[bool, str]:
    """Policy, separate from authenticity: does `band` meet the surface's `minimum`?

    `minimum` None = no gate. `unproven` is refused unless `allow_unproven` (a surface that gates on band would
    otherwise refuse EVERY newly hatched agent, including a brand-new first-party publisher: decide on purpose).
    Prefer the signer's LIVE band (registry drop detail / Eternitas) over the signing-time snapshot in the manifest.
    """
    if minimum is None:
        return True, "ok"
    if minimum not in BAND_ORDER:
        raise ValueError(f"minimum must be one of {BAND_ORDER}")
    if band == "unproven":
        return (True, "ok") if allow_unproven else (False, "band_unproven")
    if band not in BAND_ORDER:
        return False, "band_unknown"
    return (True, "ok") if BAND_ORDER.index(band) >= BAND_ORDER.index(minimum) else (False, "band_below_minimum")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    passport: str | None = None
    kid: str | None = None
    integrity_band: str | None = None  # as CLAIMED in the manifest; a surface should read the live band itself


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 thumbprint of an EC JWK."""
    canon = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
                       separators=(",", ":"), sort_keys=True)
    return _b64url(hashlib.sha256(canon.encode("utf-8")).digest())


def _no(reason: str) -> Verdict:
    return Verdict(False, reason)


def verify_signed_drop(
    manifest: dict[str, Any],
    bundle_sha256: str,
    *,
    keys: list[dict[str, Any]] | None,
    revoked_kids: Iterable[str] = (),
    revoked_passports: Iterable[str] = (),
    suspended_passports: Iterable[str] = (),
    bundle_bytes: bytes | None = None,
) -> Verdict:
    """Verify `manifest` (with its `signature` block) against the registry's `bundle_sha256`.

    keys: the signer passport's keys as returned by Eternitas (`/api/v1/bots/{passport}/keys`), or None when
    Eternitas does not know the passport (404). If the keys, the CRL or the suspension feed could not be
    fetched, do NOT call this: refuse to install (fail closed).
    bundle_bytes: the downloaded bundle; when given, its sha256 must equal `bundle_sha256` first.
    """
    if bundle_bytes is not None and hashlib.sha256(bundle_bytes).hexdigest() != bundle_sha256:
        return _no("bundle_sha_mismatch")

    sig = manifest.get("signature")
    if not isinstance(sig, dict):
        return _no("no_signature")
    if sig.get("algorithm") != "ES256":
        return _no("bad_algorithm")
    signer = sig.get("signer") or {}
    passport = signer.get("passport")
    if not passport:
        return _no("no_passport")
    jws = sig.get("jws")
    if not isinstance(jws, str):
        return _no("no_jws")

    sans = {k: v for k, v in manifest.items() if k != "signature"}
    payload = (canonicalize(sans) + bundle_sha256).encode("utf-8")
    if sig.get("signed_digest") != "sha256:" + hashlib.sha256(payload).hexdigest():
        return _no("digest_mismatch")

    parts = jws.split(".")
    if len(parts) != 3 or parts[1] != "":
        return _no("bad_jws_format")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        raw = _b64url_decode(parts[2])
    except (ValueError, binascii.Error):
        return _no("bad_jws_format")
    if not isinstance(header, dict):
        return _no("bad_header")
    if header.get("alg") != "ES256" or header.get("typ") != JWS_TYP:
        return _no("bad_header")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return _no("bad_header")
    if header.get("passport") != passport:
        return _no("passport_mismatch")
    if len(raw) != 64:
        return _no("bad_sig_length")

    if passport in set(revoked_passports):
        return _no("passport_revoked")
    if kid in set(revoked_kids):
        return _no("key_revoked")
    if passport in set(suspended_passports):
        return _no("passport_suspended")

    if keys is None:
        return _no("unknown_passport")
    key = next((k for k in keys if isinstance(k, dict) and k.get("kid") == kid), None)
    if key is None:
        return _no("unknown_key")
    if (key.get("kty"), key.get("crv"), key.get("alg"), key.get("use")) != ("EC", "P-256", "ES256", "sig"):
        return _no("bad_key_type")
    try:
        if jwk_thumbprint(key) != kid:
            return _no("kid_mismatch")
        x = int.from_bytes(_b64url_decode(key["x"]), "big")
        y = int.from_bytes(_b64url_decode(key["y"]), "big")
        pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except (KeyError, ValueError, binascii.Error, TypeError):
        return _no("bad_key_type")
    if key.get("status") != "active" or key.get("revoked_at"):
        return _no("key_not_active")

    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        pub.verify(der, (parts[0] + "." + _b64url(payload)).encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return _no("bad_signature")
    return Verdict(True, "ok", passport=passport, kid=kid, integrity_band=signer.get("integrity_band"))
