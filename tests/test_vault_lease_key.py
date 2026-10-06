"""Strand B1.1: the agent's X25519 lease key (separate from its signing key)."""

from __future__ import annotations

import stat

import pytest

from windyfly.vault import lease_key as lk


@pytest.fixture
def path(tmp_path):
    return tmp_path / "lease_keys.json"


def test_first_use_creates_an_active_key_with_0600(path):
    e = lk.ensure_active(path=path)
    assert e["kid"].startswith("lk_") and e["status"] == "active"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert lk.ensure_active(path=path)["kid"] == e["kid"]          # stable until rotated


def test_public_jwk_has_no_private_material(path):
    j = lk.public_jwk(lk.ensure_active(path=path))
    assert set(j) == {"kty", "crv", "x", "kid", "use"} and j["crv"] == "X25519" and j["use"] == "enc"


def test_rotation_keeps_the_old_key_for_24h_then_drops_it(path):
    old = lk.ensure_active(now=1000.0, path=path)
    new = lk.rotate(now=2000.0, path=path)
    assert new["kid"] != old["kid"]
    assert lk.private_for(old["kid"], now=2000.0 + 3600, path=path) is not None       # inside the overlap
    assert lk.private_for(new["kid"], now=2000.0 + 3600, path=path) is not None
    assert lk.private_for(old["kid"], now=2000.0 + lk.OVERLAP_S + 1, path=path) is None  # overlap over
    assert lk.ensure_active(now=2000.0 + lk.OVERLAP_S + 1, path=path)["kid"] == new["kid"]
    assert len(lk._load(path)["keys"]) == 1                                            # pruned


def test_unknown_kid_means_ask_for_a_new_lease(path):
    lk.ensure_active(path=path)
    assert lk.private_for("lk_nope", path=path) is None


def test_the_private_key_round_trips(path):
    from cryptography.hazmat.primitives import serialization

    e = lk.ensure_active(path=path)
    priv = lk.private_for(e["kid"], path=path)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert lk._b64(pub) == e["public"]
