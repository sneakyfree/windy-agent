"""B1.2: the Vault's frozen known-answer vector decrypts; a wrong kid is the re-lease signal."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from windyfly.vault.lease_jwe import LeaseDecryptError, UnknownLeaseKey, decrypt_lease

# Copied from windy-contracts schema/vault/lease-jwe-vector.v1.json (frozen; TEST DATA ONLY).
VECTOR = json.loads(
    (Path(__file__).parent / "fixtures" / "lease-jwe-vector.v1.json").read_text()
)["x-vector"]


def _recipient() -> X25519PrivateKey:
    der = base64.b64decode(VECTOR["recipient_pkcs8_b64"])
    return X25519PrivateKey.from_private_bytes(der[-32:])  # PKCS8 X25519: raw key is the last 32 bytes


def _keys(kid: str):
    return _recipient() if kid == VECTOR["kid"] else None


def test_vector_decrypts() -> None:
    assert decrypt_lease(VECTOR["expect_compact"], _keys) == VECTOR["plaintext_utf8"]


def test_unknown_kid_is_the_relase_signal() -> None:
    with pytest.raises(UnknownLeaseKey) as ei:
        decrypt_lease(VECTOR["expect_compact"], lambda kid: None)
    assert ei.value.kid == VECTOR["kid"]


def test_unknown_kid_calls_lookup_once() -> None:
    calls: list[str] = []

    def lookup(kid: str):
        calls.append(kid)
        return None

    with pytest.raises(UnknownLeaseKey):
        decrypt_lease(VECTOR["expect_compact"], lookup)
    assert calls == [VECTOR["kid"]]


def test_wrong_private_key_fails_without_leaking() -> None:
    with pytest.raises(LeaseDecryptError) as ei:
        decrypt_lease(VECTOR["expect_compact"], lambda kid: X25519PrivateKey.generate())
    assert VECTOR["plaintext_utf8"] not in str(ei.value)


def test_tampered_ciphertext_and_header_fail() -> None:
    h, w, iv, ct, tag = VECTOR["expect_compact"].split(".")
    bad_ct = base64.urlsafe_b64encode(b"x" * 18).rstrip(b"=").decode()
    with pytest.raises(LeaseDecryptError):
        decrypt_lease(".".join([h, w, iv, bad_ct, tag]), _keys)
    hdr = json.loads(base64.urlsafe_b64decode(h + "=" * (-len(h) % 4)))
    hdr["kid"] = hdr["kid"]  # AAD is the exact protected string: re-encoding changes it
    h2 = base64.urlsafe_b64encode(json.dumps(hdr, indent=1).encode()).rstrip(b"=").decode()
    with pytest.raises(LeaseDecryptError):
        decrypt_lease(".".join([h2, w, iv, ct, tag]), _keys)


@pytest.mark.parametrize("junk", ["", "a.b.c", "a.b.c.d.e.f", "....", "not-a-jwe"])
def test_malformed_is_a_decrypt_error(junk: str) -> None:
    with pytest.raises(LeaseDecryptError):
        decrypt_lease(junk, _keys)


def test_wrong_algorithm_refused() -> None:
    h, w, iv, ct, tag = VECTOR["expect_compact"].split(".")
    hdr = json.loads(base64.urlsafe_b64decode(h + "=" * (-len(h) % 4)))
    hdr["alg"] = "RSA-OAEP"
    h2 = base64.urlsafe_b64encode(json.dumps(hdr).encode()).rstrip(b"=").decode()
    with pytest.raises(LeaseDecryptError, match="unsupported"):
        decrypt_lease(".".join([h2, w, iv, ct, tag]), _keys)
