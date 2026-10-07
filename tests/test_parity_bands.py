"""Revoked/suspended agent senders are refused (dark: WINDY_PARITY_BANDS=1); agents are never the owner."""
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


def test_active_agent_is_not_refused_and_gets_no_band(monkeypatch):
    calls = _trust(monkeypatch, {"status": "active", "test_identity": False, "band": "good"})
    assert parity.verdict(AGENT) is None
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX
    assert calls[0].endswith("/api/v1/trust/ET26-AB12-CD34")


def test_revoked_or_suspended_is_refused_and_sandboxed(monkeypatch):
    _trust(monkeypatch, {"status": "revoked"})
    assert parity.verdict(AGENT) == "refused"
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX
    parity._reset_for_tests()
    _trust(monkeypatch, {"status": "suspended"})
    assert parity.verdict(AGENT) == "refused"


def test_unknown_and_test_passports_are_not_refused(monkeypatch):
    _trust(monkeypatch, {"detail": "not found"}, status=404)
    assert parity.verdict(AGENT) is None
    parity._reset_for_tests()
    _trust(monkeypatch, {"status": "active", "test_identity": True})
    assert parity.verdict(TESTAGENT) is None


def test_eternitas_down_never_refuses(monkeypatch):
    def boom(url, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(parity.httpx, "get", boom)
    assert parity.verdict(AGENT) is None
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX


def test_an_agent_is_never_tofu_bound_as_owner_with_or_without_the_flag(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_IDS")
    monkeypatch.delenv("WINDY_PARITY_BANDS")
    assert identity.resolve_band("matrix", AGENT) == Band.SANDBOX  # not OWNER, not bound
    assert identity.resolve_band("matrix", "@human:x") == Band.OWNER  # first human still binds


def test_humans_and_the_owner_are_unchanged(monkeypatch):
    _trust(monkeypatch, {"status": "active"})
    assert identity.resolve_band("matrix", OWNER) == Band.OWNER
    assert identity.resolve_band("matrix", "@stranger:x") == Band.SANDBOX


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_PARITY_BANDS")
    assert parity.enabled() is False


def test_lookups_are_cached(monkeypatch):
    calls = _trust(monkeypatch, {"status": "active", "test_identity": False})
    parity.verdict(AGENT)
    parity.verdict(AGENT)
    assert len(calls) == 1
