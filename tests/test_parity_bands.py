"""Eternitas parity for agent senders (dark: WINDY_PARITY_BANDS=1)."""
import httpx
import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import identity, parity

AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"
TESTAGENT = "@agent_et26-test-aaaa:chat.windychat.ai"
OWNER = "@owner:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_PARITY_BANDS", "1")
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "owners.json"))
    parity._reset_for_tests()
    identity._reset_warnings_for_tests()


def _trust(monkeypatch, payload, status=200):
    calls = []

    def get(url, timeout=None):
        calls.append(url)
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(parity.httpx, "get", get)
    return calls


def test_valid_agent_gets_the_user_band(monkeypatch):
    calls = _trust(monkeypatch, {"status": "active", "test_identity": False, "band": "good"})
    assert identity.resolve_band("matrix", AGENT) == Band.USER
    assert calls[0].endswith("/api/v1/trust/ET26-AB12-CD34")


def test_revoked_or_suspended_is_refused_and_sandboxed(monkeypatch):
    _trust(monkeypatch, {"status": "revoked"})
    assert parity.verdict(AGENT) == "refused"
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX


def test_test_passport_and_unknown_stay_sandbox(monkeypatch):
    _trust(monkeypatch, {"status": "active", "test_identity": True})
    assert identity.resolve_band("matrix", TESTAGENT) == Band.SANDBOX
    parity._reset_for_tests()
    _trust(monkeypatch, {"detail": "not found"}, status=404)
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX


def test_eternitas_down_fails_closed(monkeypatch):
    def boom(url, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(parity.httpx, "get", boom)
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX


def test_an_agent_is_never_tofu_bound_as_owner(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_IDS")
    _trust(monkeypatch, {"status": "active", "test_identity": False})
    assert identity.resolve_band("matrix", AGENT) == Band.USER  # not OWNER, not bound
    assert identity.resolve_band("matrix", "@human:x") == Band.OWNER  # first human still binds


def test_humans_and_the_owner_are_unchanged(monkeypatch):
    _trust(monkeypatch, {"status": "active"})
    assert identity.resolve_band("matrix", OWNER) == Band.OWNER
    assert identity.resolve_band("matrix", "@stranger:x") == Band.SANDBOX


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_PARITY_BANDS")
    calls = _trust(monkeypatch, {"status": "active"})
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX
    assert calls == []


def test_lookups_are_cached(monkeypatch):
    calls = _trust(monkeypatch, {"status": "active", "test_identity": False})
    parity.verdict(AGENT)
    parity.verdict(AGENT)
    assert len(calls) == 1
