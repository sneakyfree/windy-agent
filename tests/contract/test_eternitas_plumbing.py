"""Contract tests for the P1-E1 + P1-E2 cleanups.

P1-E1: ETERNITAS_URL is canonical; ETERNITAS_API_URL still works
       but emits a deprecation warning exactly once.

P1-E2: _resolve_windy_identity_id cascades env var → owner_id →
       JWT sub claim; online hatch without any resolvable identity
       fails loud.
"""

from __future__ import annotations

import base64
import json
import warnings

import pytest

from windyfly.auth.jwt_claims import identity_from_jwt, read_jwt_claims
from windyfly.eternitas.url import (
    reset_deprecation_warning_for_tests,
    resolve_eternitas_url,
)
from windyfly.hatch_orchestrator import (
    HatchResult,
    _resolve_windy_identity_id,
    _step_link_passport,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ETERNITAS_URL", "ETERNITAS_API_URL", "WINDY_IDENTITY_ID",
             "WINDY_JWT", "OWNER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    reset_deprecation_warning_for_tests()


def _jwt_with(payload: dict) -> str:
    """Build a test JWT with the given payload. Signature is junk."""
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "ES256"}).encode())
    body = b64(json.dumps(payload).encode())
    sig = b64(b"sig-not-verified")
    return f"{header}.{body}.{sig}"


class TestResolveEternitasUrl:
    def test_returns_empty_when_neither_set(self):
        assert resolve_eternitas_url() == ""

    def test_prefers_canonical_over_legacy(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_URL", "https://canonical.example")
        monkeypatch.setenv("ETERNITAS_API_URL", "https://legacy.example")
        assert resolve_eternitas_url() == "https://canonical.example"

    def test_falls_back_to_legacy_with_deprecation_warning(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_API_URL", "https://legacy.example")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            url = resolve_eternitas_url()
        assert url == "https://legacy.example"
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecations
        assert "ETERNITAS_API_URL" in str(deprecations[0].message)

    def test_deprecation_warning_fires_once(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_API_URL", "https://legacy.example")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            resolve_eternitas_url()
            resolve_eternitas_url()
            resolve_eternitas_url()
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 1

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_URL", "https://x.test///")
        assert resolve_eternitas_url() == "https://x.test"

    def test_default_when_neither_set(self):
        assert resolve_eternitas_url("https://fallback.test") == "https://fallback.test"


class TestJwtClaims:
    def test_reads_sub_from_jwt(self):
        t = _jwt_with({"sub": "wi_abc"})
        claims = read_jwt_claims(t)
        assert claims and claims["sub"] == "wi_abc"

    def test_accepts_bearer_prefix(self):
        t = _jwt_with({"sub": "wi_abc"})
        assert identity_from_jwt("Bearer " + t) == "wi_abc"

    def test_prefers_windy_identity_id_over_sub(self):
        t = _jwt_with({"sub": "wi_from_sub", "windy_identity_id": "wi_from_claim"})
        assert identity_from_jwt(t) == "wi_from_claim"

    def test_returns_empty_on_malformed(self):
        assert identity_from_jwt("not.a.jwt") == ""
        assert identity_from_jwt("") == ""
        assert identity_from_jwt("one.two") == ""
        # Junk base64
        assert identity_from_jwt("aaaa.bbbb.cccc") == ""


class TestResolveIdentityCascade:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("WINDY_IDENTITY_ID", "wi_env")
        monkeypatch.setenv("WINDY_JWT", _jwt_with({"sub": "wi_jwt"}))
        assert _resolve_windy_identity_id("wi_owner") == "wi_env"

    def test_owner_id_when_no_env(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", _jwt_with({"sub": "wi_jwt"}))
        assert _resolve_windy_identity_id("wi_owner") == "wi_owner"

    def test_jwt_sub_when_neither(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", _jwt_with({"sub": "wi_jwt"}))
        assert _resolve_windy_identity_id("") == "wi_jwt"

    def test_empty_when_none_available(self):
        assert _resolve_windy_identity_id("") == ""


class TestLinkPassportOnlineFailLoud:
    async def test_online_without_identity_appends_error(self, monkeypatch):
        # WINDY_JWT is set (→ online) but with no sub claim and no
        # identity id env var — must fail loud, not silently skip.
        monkeypatch.setenv("WINDY_JWT", _jwt_with({"iss": "x"}))
        monkeypatch.delenv("WINDY_IDENTITY_ID", raising=False)
        result = HatchResult(agent_name="t", owner_name="", passport_id="ET26-X")

        await _step_link_passport(result, owner_id="")

        assert any(
            "Link-passport: online hatch but no WINDY_IDENTITY_ID" in e
            for e in result.errors
        ), f"Expected loud error, got {result.errors!r}"

    async def test_offline_without_identity_is_silent_skip(self, monkeypatch):
        # No JWT, no identity — intentional offline hatch; do not
        # pollute errors.
        monkeypatch.delenv("WINDY_JWT", raising=False)
        monkeypatch.delenv("WINDY_IDENTITY_ID", raising=False)
        result = HatchResult(agent_name="t", owner_name="", passport_id="ET26-X")

        await _step_link_passport(result, owner_id="")

        assert not result.errors
