"""Decrypt a Vault lease JWE (strand gene B1.2; Vault plan C7, V7.6 c2d).

A lease arrives as a compact JWE: ECDH-ES+A256KW (X25519) with A256GCM and a ``kid`` header
naming which of the agent's lease keys it was encrypted to. Built on ``cryptography`` (already a
dependency, no new package in the release); the Vault's frozen known-answer vector
(windy-contracts ``schema/vault/lease-jwe-vector.v1.json``) is the arbiter of correctness.

An unknown ``kid`` is not an error in the crypto sense: it means the lease was issued to a key
this agent no longer holds, so the caller asks for ONE new lease (``UnknownLeaseKey``). Every other
failure is ``LeaseDecryptError``. Messages never contain key or lease material.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.concatkdf import ConcatKDFHash
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap

ALG = "ECDH-ES+A256KW"
ENC = "A256GCM"


class LeaseDecryptError(Exception):
    """The lease could not be opened (malformed, wrong algorithm, tampered, or wrong key)."""


class UnknownLeaseKey(LeaseDecryptError):
    """The ``kid`` names a key this agent does not hold: ask for one new lease."""

    def __init__(self, kid: str) -> None:
        super().__init__("lease key not held")
        self.kid = kid


def _unb64(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError as exc:
        raise LeaseDecryptError("malformed lease") from exc


def _field(header: dict[str, object], name: str) -> bytes:
    v = header.get(name)
    return _unb64(v) if isinstance(v, str) else b""


def decrypt_lease(compact: str, key_for_kid: Callable[[str], X25519PrivateKey | None]) -> str:
    """Return the lease value. ``key_for_kid`` is ``lease_key.private_for`` (None if not held)."""
    parts = compact.strip().split(".")
    if len(parts) != 5 or not all(parts[i] for i in (0, 1, 2, 3)):
        raise LeaseDecryptError("malformed lease")
    protected_b64, wrapped_b64, iv_b64, ct_b64, tag_b64 = parts
    try:
        header = json.loads(_unb64(protected_b64))
    except ValueError as exc:
        raise LeaseDecryptError("malformed lease") from exc
    if not isinstance(header, dict) or header.get("alg") != ALG or header.get("enc") != ENC:
        raise LeaseDecryptError("unsupported lease algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise LeaseDecryptError("malformed lease")
    priv = key_for_kid(kid)
    if priv is None:
        raise UnknownLeaseKey(kid)
    epk = header.get("epk")
    if (not isinstance(epk, dict) or epk.get("kty") != "OKP" or epk.get("crv") != "X25519"
            or not isinstance(epk.get("x"), str)):
        raise LeaseDecryptError("malformed lease")
    try:
        shared = priv.exchange(X25519PublicKey.from_public_bytes(_unb64(epk["x"])))
        # RFC 7518 4.6.2 Concat KDF: AlgorithmID = alg (the key wrap), 256-bit KEK.
        def lv(b: bytes) -> bytes:
            return len(b).to_bytes(4, "big") + b

        other = lv(ALG.encode("ascii")) + lv(_field(header, "apu")) + lv(_field(header, "apv"))
        other += (256).to_bytes(4, "big")
        kdf = ConcatKDFHash(algorithm=hashes.SHA256(), length=32, otherinfo=other)
        kek = kdf.derive(shared)
        cek = aes_key_unwrap(kek, _unb64(wrapped_b64))
        plain = AESGCM(cek).decrypt(
            _unb64(iv_b64), _unb64(ct_b64) + _unb64(tag_b64), protected_b64.encode("ascii"))
        return plain.decode("utf-8")
    except (InvalidTag, InvalidUnwrap, ValueError) as exc:
        raise LeaseDecryptError("lease could not be opened") from exc
