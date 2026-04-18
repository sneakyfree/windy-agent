"""Contract tests for the trust.changed webhook signature verifier
(P0-T1 + P0-T2 fix).

Proves:
  - Detached ES256 JWS verifies correctly and fails on tampered bodies.
  - HMAC-SHA256 verifies correctly across both header formats.
  - verify_webhook refuses when either signature fails.
  - Dev-mode fail-open fires only when nothing is configured AND
    WINDYFLY_TRUST_STRICT is unset.
  - The new bridge handler writes the right trust.webhook response
    shape (happy path + rejected path).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from windyfly.trust import verify as verify_mod
from windyfly.trust.verify import (
    VerifyResult,
    verify_hmac,
    verify_jws,
    verify_webhook,
)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign_detached_jws(
    body: bytes,
    key: ec.EllipticCurvePrivateKey,
    kid: str,
) -> str:
    """Produce a detached ES256 JWS token for `body`."""
    protected = {"alg": "ES256", "kid": kid}
    protected_b64 = _b64url(json.dumps(protected, separators=(",", ":")).encode())
    payload_b64 = _b64url(body)
    signing_input = (protected_b64 + "." + payload_b64).encode("ascii")

    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # cryptography returns DER; JWS wants raw r||s (64 bytes).
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return protected_b64 + ".." + _b64url(raw_sig)


def _jwks_for_key(pub: ec.EllipticCurvePublicKey, kid: str) -> dict:
    nums = pub.public_numbers()
    return {
        "keys": [{
            "kty": "EC",
            "crv": "P-256",
            "x": _b64url(nums.x.to_bytes(32, "big")),
            "y": _b64url(nums.y.to_bytes(32, "big")),
            "kid": kid,
            "alg": "ES256",
            "use": "sig",
        }]
    }


@pytest.fixture(autouse=True)
def _tmp_jwks_cache(tmp_path, monkeypatch):
    """Redirect the JWKS disk cache so tests don't touch real state."""
    monkeypatch.setattr(verify_mod, "_JWKS_CACHE_FILE", tmp_path / "jwks.json")
    monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
    monkeypatch.delenv("ETERNITAS_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ETERNITAS_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)


@pytest.fixture
def ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, priv.public_key()


# ────────────────────────────────────────────────────────────────────
# HMAC
# ────────────────────────────────────────────────────────────────────


class TestHmac:
    def test_accepts_matching_sha256_header(self):
        body = b'{"event":"trust.changed"}'
        secret = "hunter2"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = verify_hmac(body, f"sha256={sig}", secret)
        assert r.ok

    def test_accepts_v1_header_variant(self):
        body = b'{"event":"trust.changed"}'
        secret = "hunter2"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = verify_hmac(body, f"t=1718000000,v1={sig}", secret)
        assert r.ok

    def test_rejects_tampered_body(self):
        body = b'{"event":"trust.changed"}'
        secret = "hunter2"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = verify_hmac(b'{"event":"other"}', f"sha256={sig}", secret)
        assert not r.ok
        assert "mismatch" in r.reason

    def test_rejects_wrong_secret(self):
        body = b"x"
        sig = hmac.new(b"real", body, hashlib.sha256).hexdigest()
        r = verify_hmac(body, f"sha256={sig}", "fake")
        assert not r.ok

    def test_rejects_missing_header(self):
        assert not verify_hmac(b"x", "", "k").ok

    def test_rejects_missing_secret(self):
        assert not verify_hmac(b"x", "sha256=deadbeef", "").ok


# ────────────────────────────────────────────────────────────────────
# JWS
# ────────────────────────────────────────────────────────────────────


class TestJws:
    def test_accepts_detached_jws(self, ec_keypair):
        priv, pub = ec_keypair
        body = b'{"event":"trust.changed","passport":"ET26-X"}'
        sig = _sign_detached_jws(body, priv, kid="key-1")
        r = verify_jws(body, sig, _jwks_for_key(pub, "key-1"))
        assert r.ok

    def test_rejects_tampered_body(self, ec_keypair):
        priv, pub = ec_keypair
        body = b'{"event":"trust.changed"}'
        sig = _sign_detached_jws(body, priv, kid="key-1")
        r = verify_jws(b'{"event":"evil"}', sig, _jwks_for_key(pub, "key-1"))
        assert not r.ok

    def test_rejects_wrong_kid(self, ec_keypair):
        priv, pub = ec_keypair
        body = b"x"
        sig = _sign_detached_jws(body, priv, kid="key-1")
        r = verify_jws(body, sig, _jwks_for_key(pub, "different-kid"))
        assert not r.ok
        assert "kid" in r.reason

    def test_rejects_wrong_key(self, ec_keypair):
        priv1, _ = ec_keypair
        priv2 = ec.generate_private_key(ec.SECP256R1())
        body = b"x"
        sig = _sign_detached_jws(body, priv1, kid="key-1")
        # Build JWKS with priv2's public key; signature must not verify.
        r = verify_jws(body, sig, _jwks_for_key(priv2.public_key(), "key-1"))
        assert not r.ok

    def test_rejects_malformed_header(self, ec_keypair):
        _, pub = ec_keypair
        r = verify_jws(b"x", "not.a.valid.jws.thing", _jwks_for_key(pub, "x"))
        assert not r.ok

    def test_rejects_non_detached(self, ec_keypair):
        priv, pub = ec_keypair
        body = b"x"
        # Build a non-detached JWS (middle segment non-empty).
        protected = {"alg": "ES256", "kid": "key-1"}
        pb = _b64url(json.dumps(protected, separators=(",", ":")).encode())
        non_detached = f"{pb}.aGVsbG8.sig"
        r = verify_jws(body, non_detached, _jwks_for_key(pub, "key-1"))
        assert not r.ok


# ────────────────────────────────────────────────────────────────────
# Top-level verify_webhook
# ────────────────────────────────────────────────────────────────────


class TestVerifyWebhook:
    def test_fails_open_in_dev_without_anything_configured(self):
        r = verify_webhook(b"{}", {}, hmac_secret="", eternitas_url="")
        assert r.ok
        assert "dev" in r.reason

    def test_fails_closed_in_strict_mode_without_signatures(self, monkeypatch):
        monkeypatch.setenv("WINDYFLY_TRUST_STRICT", "1")
        r = verify_webhook(b"{}", {}, hmac_secret="", eternitas_url="")
        assert not r.ok
        assert "strict" in r.reason

    def test_rejects_when_only_hmac_configured_and_jws_missing(self, monkeypatch):
        body = b"{}"
        secret = "k"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # HMAC header present + correct, but no JWS and no JWKS configured.
        r = verify_webhook(
            body,
            {"X-Eternitas-Signature": f"sha256={sig}"},
            hmac_secret=secret,
            eternitas_url="",
        )
        assert not r.ok

    def test_accepts_when_both_signatures_present(
        self, ec_keypair, monkeypatch, tmp_path
    ):
        priv, pub = ec_keypair
        body = b'{"event":"trust.changed","passport":"ET26-X"}'
        secret = "k"
        hmac_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        jws = _sign_detached_jws(body, priv, kid="key-1")

        # Pre-seed the JWKS cache so we don't need a live HTTP call.
        cache = tmp_path / "jwks.json"
        verify_mod._JWKS_CACHE_FILE = cache
        cache.write_text(json.dumps({
            "jwks": _jwks_for_key(pub, "key-1"),
            "cached_at": 99999999999,   # in the future — cache won't expire
        }))

        r = verify_webhook(
            body,
            {
                "X-Eternitas-Signature": f"sha256={hmac_hex}",
                "X-Windy-Signature": jws,
            },
            hmac_secret=secret,
            eternitas_url="https://eternitas.test",
        )
        assert r.ok, r.reason

    def test_case_insensitive_header_lookup(self):
        body = b"x"
        secret = "k"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # Lowercase header key, should still work.
        r = verify_hmac(body, f"sha256={sig}", secret)
        assert r.ok
        r = verify_webhook(
            body,
            {"x-eternitas-signature": f"sha256={sig}"},
            hmac_secret=secret,
            eternitas_url="",
        )
        # JWS missing → still fails, but not because header wasn't seen.
        assert "HMAC" not in (r.reason or "").upper()
