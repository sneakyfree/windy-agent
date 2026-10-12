"""R6: Vault's kid, the registration body, and the decrypt refusals (Windy Vault review, 10-12).

The agent named its lease keys "lk_<random>", but Vault names a key by
hex(sha256(utf8("wv-keyid|") || raw32)[:8]) and stamps that 16-hex kid on every lease, so every
real lease would have been an UnknownLeaseKey. The registration body is exactly
{"public_key": <standard padded base64 of the 32 raw X25519 bytes>}, never a JWK. Leases carry
anything Vault's reference decrypt refuses (another enc, zip/crit, apu/apv, a bad epk, an all-zero
shared secret): refused here too. Known answers from Vault's own keyId. Test data only.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.concatkdf import ConcatKDFHash
from cryptography.hazmat.primitives.keywrap import aes_key_wrap

from tests.test_vault_lease_jwe import VECTOR
from windyfly.vault import lease_key as lk
from windyfly.vault.lease_jwe import LeaseDecryptError, decrypt_lease

SECRET = "the-lease-value-never-in-an-error"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _seal(pub: X25519PublicKey, kid: str, plaintext: str = SECRET, *, epk_x: bytes | None = None,
          **extra: object) -> str:
    """A test-side ECDH-ES+A256KW / A256GCM sealer (what Vault does), with optional extra header fields."""
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    header = {"alg": "ECDH-ES+A256KW", "enc": "A256GCM", "kid": kid,
              "epk": {"kty": "OKP", "crv": "X25519", "x": _b64u(epk_x if epk_x is not None else eph_pub)}}
    header.update(extra)
    shared = eph.exchange(pub)

    def lv(b: bytes) -> bytes:
        return len(b).to_bytes(4, "big") + b

    apu = base64.urlsafe_b64decode(str(header.get("apu", "")) + "==") if "apu" in header else b""
    apv = base64.urlsafe_b64decode(str(header.get("apv", "")) + "==") if "apv" in header else b""
    other = lv(b"ECDH-ES+A256KW") + lv(apu) + lv(apv) + (256).to_bytes(4, "big")
    kek = ConcatKDFHash(algorithm=hashes.SHA256(), length=32, otherinfo=other).derive(shared)
    cek, iv = os.urandom(32), os.urandom(12)
    protected = _b64u(json.dumps(header).encode())
    ct = AESGCM(cek).encrypt(iv, plaintext.encode(), protected.encode("ascii"))
    return ".".join([protected, _b64u(aes_key_wrap(kek, cek)), _b64u(iv), _b64u(ct[:-16]), _b64u(ct[-16:])])


@pytest.fixture
def path(tmp_path):
    return tmp_path / "lease_keys.json"


def _pub(entry) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(lk._unb64(entry["public"]))


# ── the kid and the wire body ──────────────────────────────────────────────────────────

def test_the_kid_is_vaults_own_hash():
    assert lk.vault_kid(bytes(range(32))) == "cbc1a46f6522e661"                  # Vault crypto.v1 key_id
    assert lk.vault_kid(bytes.fromhex(VECTOR["recipient_pub_hex"])) == "36f519c45982e917"
    with pytest.raises(ValueError):
        lk.vault_kid(b"\x01" * 31)


def test_registration_body_is_only_the_raw_public_key(path):
    vector_entry = {"public": _b64u(bytes.fromhex(VECTOR["recipient_pub_hex"]))}
    assert lk.registration_body(vector_entry) == {"public_key": "3CzKMejkO72R3/fkdcyjNH60eBB9W9dlq6SuSjDDXUQ="}
    body = lk.registration_body(lk.ensure_active(path=path))
    assert set(body) == {"public_key"} and len(base64.b64decode(body["public_key"], validate=True)) == 32


def test_the_kid_vault_returns_is_stored_and_a_mismatch_is_refused(path):
    e = lk.ensure_active(path=path)
    assert len(e["kid"]) == 16 and e["kid"] == lk.vault_kid(lk._unb64(e["public"]))
    assert lk.register(lambda body: (200, {"kid": e["kid"]}), path=path) == e["kid"]
    assert lk.ensure_active(path=path).get("registered_at")
    other = tmp = path.with_name("other.json")
    own = lk.ensure_active(path=other)["kid"]
    for reply in [(200, {"kid": "0" * 16}), (200, {"kid": "LK_NOT_HEX_123456"}), (409, {"kid": "x"}), (500, {}),
                  (503, {"kid": own})]:                                          # a refusal even with the right kid
        with pytest.raises(lk.LeaseKeyRegistrationError):
            lk.register(lambda body, r=reply: r, path=tmp)
    assert not lk.ensure_active(path=other).get("registered_at")                 # nothing stored


def test_a_lease_sealed_to_the_16_hex_kid_decrypts(path):
    e = lk.ensure_active(path=path)
    lk.register(lambda body: (200, {"kid": e["kid"]}), path=path)
    lease = _seal(_pub(e), e["kid"])
    assert decrypt_lease(lease, lambda kid: lk.private_for(kid, path=path), lease_kid=e["kid"]) == SECRET
    with pytest.raises(LeaseDecryptError, match="kid"):
        decrypt_lease(lease, lambda kid: lk.private_for(kid, path=path), lease_kid="0" * 16)


def test_an_old_lk_key_file_gets_vaults_kid(path):
    e = lk.ensure_active(path=path)
    data = json.loads(path.read_text())
    data["keys"][0]["kid"] = "lk_0123456789ab"                                     # a file from before R6
    path.write_text(json.dumps(data))
    assert lk.ensure_active(path=path)["kid"] == e["kid"]


# ── decrypt refusals (Vault's reference jweDecrypt refuses these) ──────────────────────

@pytest.fixture
def held(path):
    e = lk.ensure_active(path=path)
    return e, (lambda kid: lk.private_for(kid, path=path))


def test_enc_other_than_a256gcm_is_refused(held):
    e, keys = held
    lease = _seal(_pub(e), e["kid"])
    h, *rest = lease.split(".")
    hdr = json.loads(base64.urlsafe_b64decode(h + "=="))
    hdr["enc"] = "A128GCM"
    with pytest.raises(LeaseDecryptError, match="unsupported"):
        decrypt_lease(".".join([_b64u(json.dumps(hdr).encode()), *rest]), keys)


@pytest.mark.parametrize("extra", [{"zip": "DEF"}, {"crit": ["exp"]}])
def test_zip_or_crit_is_refused(held, extra):
    e, keys = held
    with pytest.raises(LeaseDecryptError, match="unsupported"):
        decrypt_lease(_seal(_pub(e), e["kid"], **extra), keys)


@pytest.mark.parametrize("extra", [{"apu": _b64u(b"alice")}, {"apv": _b64u(b"bob")}])
def test_apu_or_apv_is_refused(held, extra):
    e, keys = held
    with pytest.raises(LeaseDecryptError, match="unsupported"):
        decrypt_lease(_seal(_pub(e), e["kid"], **extra), keys)


@pytest.mark.parametrize("epk", [{"kty": "EC", "crv": "X25519"}, {"kty": "OKP", "crv": "X448"}, {"x31": True}])
def test_a_bad_epk_is_refused(held, epk):
    e, keys = held
    if epk.get("x31"):
        lease = _seal(_pub(e), e["kid"], epk_x=b"\x09" * 31)
    else:
        lease = _seal(_pub(e), e["kid"])
        h, *rest = lease.split(".")
        hdr = json.loads(base64.urlsafe_b64decode(h + "=="))
        hdr["epk"].update(epk)
        lease = ".".join([_b64u(json.dumps(hdr).encode()), *rest])
    with pytest.raises(LeaseDecryptError, match="malformed"):
        decrypt_lease(lease, keys)


def test_an_all_zero_shared_secret_is_refused(held):
    e, keys = held
    lease = _seal(_pub(e), e["kid"], epk_x=bytes(32))                              # u = 0: low-order point
    with pytest.raises(LeaseDecryptError) as ei:
        decrypt_lease(lease, keys)
    assert SECRET not in str(ei.value)
