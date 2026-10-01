"""Drift test for the vendored Windy Drops verifier (src/windyfly/_vendor/windy_drops/SOURCE).

Every conformance vector in vectors.json (and every policy vector) must reproduce its expected reason
through the vendored ``verify_signed_drop`` / ``check_min_band``. The vendored files are pinned by
sha256 so a hand edit fails here; re-vendor from windy-drops instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from windyfly._vendor.windy_drops.verify import REASONS, check_min_band, verify_signed_drop

VENDOR = Path(__file__).resolve().parents[1] / "src" / "windyfly" / "_vendor" / "windy_drops"
DATA = json.loads((VENDOR / "vectors.json").read_text(encoding="utf-8"))


def _pins() -> dict[str, str]:
    pins = {}
    for line in (VENDOR / "SOURCE").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64 and "<-" in parts:
            pins[parts[1]] = parts[0]
    return pins


def test_source_names_the_commit_and_pins_every_vendored_file():
    src = (VENDOR / "SOURCE").read_text(encoding="utf-8")
    assert "sneakyfree/windy-drops" in src and "21ff089" in src
    pins = _pins()
    assert set(pins) == {"vectors.json", "README.md", "verify.py", "canonical.py"}
    for name, digest in pins.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, (
            f"{name} drifted from windy-drops 21ff089: re-vendor it, never hand-edit"
        )


def test_vector_file_is_the_expected_format():
    assert DATA["format"] == "windy-drops-jws-vectors/1"
    assert len(DATA["vectors"]) >= 21 and len(DATA["policy_vectors"]) >= 10


@pytest.mark.parametrize("vec", DATA["vectors"], ids=[v["name"] for v in DATA["vectors"]])
def test_every_signature_vector(vec):
    bundle = base64.b64decode(vec["bundle_b64"]) if vec.get("bundle_b64") else None
    verdict = verify_signed_drop(
        vec["manifest"],
        vec["bundle_sha256"],
        keys=vec["keys"],
        revoked_kids=vec.get("revoked_kids", []),
        revoked_passports=vec.get("revoked_passports", []),
        suspended_passports=vec.get("suspended_passports", []),
        bundle_bytes=bundle,
    )
    assert verdict.reason in REASONS
    assert (verdict.ok, verdict.reason) == (vec["expect"]["ok"], vec["expect"]["reason"])
    if "policy" in vec:  # an authentic signature that a band gate must still refuse
        pol = vec["policy"]
        band = (vec["manifest"].get("signature") or {}).get("signer", {}).get("integrity_band")
        got = check_min_band(band, pol["min_band"], allow_unproven=pol["allow_unproven"])
        assert got == (pol["expect"]["ok"], pol["expect"]["reason"])


@pytest.mark.parametrize(
    "vec", DATA["policy_vectors"], ids=[v["name"] for v in DATA["policy_vectors"]],
)
def test_every_policy_vector(vec):
    got = check_min_band(vec["band"], vec["min_band"], allow_unproven=vec["allow_unproven"])
    assert got == (vec["expect"]["ok"], vec["expect"]["reason"])


def test_every_reason_code_is_covered_by_some_vector():
    seen = {v["expect"]["reason"] for v in DATA["vectors"]}
    assert seen == set(REASONS)
