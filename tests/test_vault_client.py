"""The dark Vault lease client (windy-contracts agent-routes.v1): capabilities, lease (decrypted locally),
release, lease-key registration. Fixtures: the contract's capabilities example and the frozen lease JWE vector."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from windyfly.vault import client as vc

FIX = Path(__file__).parent / "fixtures"
VECTOR = json.loads((FIX / "lease-jwe-vector.v1.json").read_text())["x-vector"]
CAPS = json.loads((FIX / "vault-agent-routes.capabilities.response.json").read_text())


def _recipient() -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(base64.b64decode(VECTOR["recipient_pkcs8_b64"])[-32:])


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setenv(vc.ENV_FLAG, "1")
    monkeypatch.setenv(vc.ENV_URL, "https://vault.example")
    sent: list[dict] = []
    forgot = []
    monkeypatch.setattr(vc.service_auth, "agent_headers",
                        lambda aud, m, u: {"Authorization": f"Bearer tok-{aud}", "DPoP": f"proof-{m}-{u}"})
    monkeypatch.setattr(vc.service_auth, "forget_token", lambda: forgot.append(1))
    answers: list = []

    def request(method, url, headers=None, json=None, timeout=None):
        sent.append({"method": method, "url": url, "headers": dict(headers), "json": json})
        status, body = answers.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(vc.httpx, "request", request)
    return sent, answers, forgot


def _lease_body(kid=None):
    return {"lease_id": "lse_l6kpl4pyah7use6z", "item_id": "itm_x", "mode": "static_key",
            "value_jwe": VECTOR["expect_compact"], "kid": kid or VECTOR["kid"], "issued_at": 1800000000,
            "expires_at": 1800000900, "revocable": False}


def test_off_by_default_and_without_a_url(monkeypatch):
    monkeypatch.delenv(vc.ENV_FLAG, raising=False)
    monkeypatch.setenv(vc.ENV_URL, "https://vault.example")
    assert vc.enabled() is False
    monkeypatch.setenv(vc.ENV_FLAG, "1")
    monkeypatch.delenv(vc.ENV_URL)
    assert vc.enabled() is False
    with pytest.raises(vc.VaultError) as e:
        vc.capabilities()
    assert e.value.code == "unavailable"


def test_capabilities_reads_the_contract_example_and_signs_a_get(vault):
    sent, answers, _ = vault
    answers.append((200, CAPS))
    items = vc.capabilities()
    assert [i["label"] for i in items] == ["Soul key", "OpenRouter"] and items[0]["expires_at"] is None
    req = sent[0]
    assert req["method"] == "GET" and req["url"] == "https://vault.example/v1/agent/capabilities"
    assert req["headers"]["Authorization"] == "Bearer tok-windy-vault"
    assert req["headers"]["DPoP"] == "proof-GET-https://vault.example/v1/agent/capabilities"


def test_lease_decrypts_locally_and_never_shows_the_value(vault, monkeypatch, caplog):
    sent, answers, _ = vault
    monkeypatch.setattr(vc.lease_key, "private_for", lambda kid: _recipient() if kid == VECTOR["kid"] else None)
    answers.append((200, _lease_body()))
    with caplog.at_level(logging.DEBUG):
        got = vc.lease("itm_x")
    assert got.value == VECTOR["plaintext_utf8"] and got.lease_id.startswith("lse_")
    assert sent[0]["json"] == {"item_id": "itm_x"} and sent[0]["headers"]["Content-Type"] == "application/json"
    assert VECTOR["plaintext_utf8"] not in repr(got) and VECTOR["plaintext_utf8"] not in caplog.text


def test_a_lease_to_a_key_we_no_longer_hold_reregisters_once(vault, monkeypatch):
    sent, answers, _ = vault
    held = {"have": False}
    monkeypatch.setattr(vc.lease_key, "private_for",
                        lambda kid: _recipient() if held["have"] and kid == VECTOR["kid"] else None)
    regs = []

    def register(put):
        regs.append(1)
        held["have"] = True
        return VECTOR["kid"]

    monkeypatch.setattr(vc.lease_key, "register", register)
    answers.extend([(200, _lease_body()), (200, _lease_body())])
    assert vc.lease("itm_x").value == VECTOR["plaintext_utf8"] and regs == [1]

    held["have"] = False
    monkeypatch.setattr(vc.lease_key, "register", lambda put: VECTOR["kid"])  # still not held: give up
    answers.extend([(200, _lease_body()), (200, _lease_body())])
    with pytest.raises(vc.VaultError) as e:
        vc.lease("itm_x")
    assert e.value.code == "unknown_lease_key"


def test_a_refused_token_is_reminted_once(vault):
    sent, answers, forgot = vault
    answers.extend([(401, {"error": "dpop_replay"}), (200, CAPS)])
    assert len(vc.capabilities()) == 2 and forgot == [1] and len(sent) == 2


def test_other_refusals_carry_vaults_code_and_are_not_retried(vault):
    sent, answers, forgot = vault
    answers.append((403, {"error": "grant_revoked", "say": "That isn't shared with your agent any more."}))
    with pytest.raises(vc.VaultError) as e:
        vc.lease("itm_x")
    assert (e.value.code, e.value.status) == ("grant_revoked", 403) and forgot == [] and len(sent) == 1


@pytest.mark.parametrize("released", [True, False])
def test_release(vault, released):
    sent, answers, _ = vault
    answers.append((200, {"released": released}))
    assert vc.release("lse_abc") is released
    assert sent[0]["method"] == "POST" and sent[0]["url"].endswith("/v1/agent/lease/lse_abc/release")


def test_register_lease_key_puts_through_the_agent_route(vault, monkeypatch):
    sent, answers, _ = vault
    seen = {}

    def register(put):
        seen["reply"] = put({"public_key": "AAAA"})
        return "65df274c2f8401b7"

    monkeypatch.setattr(vc.lease_key, "register", register)
    answers.append((200, {"kid": "65df274c2f8401b7"}))
    assert vc.register_lease_key() == "65df274c2f8401b7"
    assert seen["reply"] == (200, {"kid": "65df274c2f8401b7"})
    assert sent[0]["method"] == "PUT" and sent[0]["url"].endswith("/v1/agent/lease-key")
