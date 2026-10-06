"""Strand A0.3: one mode-B implementation for Mind and the Vault."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from windyfly.agent import service_auth as sa


@pytest.fixture
def ak(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    minted, proofs, cleared = [], [], []
    monkeypatch.setattr(ak, "request_agent_token", lambda aud, **k: minted.append(aud) or {"token": f"tok-{len(minted)}"})
    monkeypatch.setattr(ak, "service_dpop", lambda htm, url, **k: proofs.append((htm, url)) or f"proof-{len(proofs)}")
    monkeypatch.setattr(ak, "clear_token_cache", lambda: cleared.append(1))
    return minted, proofs, cleared


def _resp(status=200, err=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"x-vault-error": err} if err else {}
    return r


def test_aud_is_a_parameter_and_get_has_no_proof(ak):
    minted, proofs, _ = ak
    assert sa.agent_headers("windy-vault", "GET", "https://v/x") == {"Authorization": "Bearer tok-1"}
    h = sa.agent_headers("windy-vault", "POST", "https://v/lease")
    assert h == {"Authorization": "Bearer tok-2", "DPoP": "proof-1"} and minted == ["windy-vault"] * 2
    assert proofs == [("POST", "https://v/lease")]


def test_a_mint_failure_is_a_service_auth_error_with_a_code(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    def boom(aud, **k):
        raise ak.AgentTokenError("no_key", "none")

    monkeypatch.setattr(ak, "request_agent_token", boom)
    with pytest.raises(sa.ServiceAuthError) as e:
        sa.agent_headers("windy-vault", "GET", "https://v/x")
    assert e.value.code == "no_key"


def test_refused_code_only_for_remintable_401s():
    assert sa.refused_code(_resp(401, "dpop_replay"), "x-vault-error") == "dpop_replay"
    assert sa.refused_code(_resp(401, "not_authenticated"), "x-vault-error") is None
    assert sa.refused_code(_resp(403, "dpop_replay"), "x-vault-error") is None


def test_call_with_remint_retries_once_with_a_fresh_proof(ak):
    _, proofs, cleared = ak
    sent = []

    def send(h):
        sent.append(h)
        return _resp(401, "dpop_replay") if len(sent) == 1 else _resp(200)

    r = sa.call_with_remint("windy-vault", "POST", "https://v/lease", send, "x-vault-error")
    assert r.status_code == 200 and cleared == [1]
    assert [h["DPoP"] for h in sent] == ["proof-1", "proof-2"]


def test_no_retry_on_an_ordinary_answer(ak):
    sent = []
    sa.call_with_remint("windy-vault", "GET", "https://v/x", lambda h: sent.append(h) or _resp(200), "x-vault-error")
    assert len(sent) == 1
