"""Windy Fly's vault.* tools conform to the SHARED contract windy-contracts schema/agent/vault-tools.v1.json 1.0.1
(Boss 10-27: one definition for Windy Fly and the Chat roster). Vendored and hash-pinned; every tool answer in every
scenario is validated against the contract's result schema for that tool, the say words are compared verbatim, and
the x-mapping table is exercised per tool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from windyfly.agent.capabilities import vault as vt
from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.vault import client as vc

DIR = Path(__file__).parent / "contracts" / "agent"
CONTRACT = json.loads((DIR / "vault-tools.v1.json").read_text())
CAPS = json.loads(
    (
        Path(__file__).parent / "fixtures" / "vault-agent-routes.capabilities.response.json"
    ).read_text()
)
TOOLS = ("vault.list", "vault.request_access", "vault.offer_save", "vault.request_status")
PENDING = {
    "request_id": "req_ynnmfmo2zwh4mmej",
    "state": "pending",
    "expires_at": 1800000600,
    "retry_after_s": 15,
}


def test_the_vendored_contract_is_the_pinned_one():
    src = (DIR / "vault-tools.v1.SOURCE").read_text().split()
    assert CONTRACT["x-version"] == "1.0.1"
    assert (
        hashlib.sha256((DIR / "vault-tools.v1.json").read_bytes()).hexdigest()
        == src[src.index("sha256") + 1]
    )


def _validate(tool: str, out: dict) -> None:
    schema = {**CONTRACT["x-tools"][tool]["result"], "$defs": CONTRACT["$defs"]}
    jsonschema.validate(out, schema)


def test_every_say_is_the_contracts_word_for_word():
    say = CONTRACT["x-refusals"]["say"]
    used = set().union(*(CONTRACT["x-refusals"]["by_tool"][t] for t in TOOLS))
    assert set(vt._SAY) == used
    assert {c: vt._SAY[c] for c in used} == {c: say[c] for c in used}


def test_the_state_words_and_the_list_note_are_the_contracts():
    for branch in CONTRACT["$defs"]["StatusResult"]["oneOf"]:
        p = branch["properties"]
        assert vt._STATE_SAY[(p["class"]["const"], p["state"]["const"])] == p["say"]["const"]
    assert len(vt._STATE_SAY) == len(CONTRACT["$defs"]["StatusResult"]["oneOf"])


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setenv(vc.ENV_FLAG, "1")
    monkeypatch.setenv(vc.ENV_URL, "https://vault.example")
    monkeypatch.setenv(vt.ENV_FLAG, "1")
    monkeypatch.setattr(
        vc.service_auth, "agent_headers", lambda aud, m, u: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(vc.service_auth, "forget_token", lambda: None)
    answers: list = []

    def request(method, url, headers=None, json=None, timeout=None):
        status, body = answers.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(vc.httpx, "request", request)
    answers.append((200, CAPS))  # the boot probe
    r = CapabilityRegistry()
    vt.register_vault_capabilities(r, {})
    return r, answers


def test_the_model_facing_descriptions_are_the_contracts(vault):
    r, _ = vault
    for t in TOOLS:
        assert r.get(t).description == CONTRACT["x-tools"][t]["description"], t


# (tool, Vault answer before the call, the call's Vault answer, args, expected code) per vault-tools.v1 x-mapping.
READY = (200, {**CAPS, "vault": "ready"})
CASES = [
    ("vault.list", None, (401, {"error": "unauthenticated"}), {}, "unavailable"),
    ("vault.list", None, (403, {"error": "forbidden"}), {}, "not_linked"),
    ("vault.list", None, (423, {"error": "vault_locked"}), {}, "vault_locked"),
    ("vault.list", None, (429, {"error": "budget_exhausted"}), {}, "rate_limited"),
    (
        "vault.list",
        None,
        (404, {"error": "not_found"}),
        {},
        "unavailable",
    ),  # capabilities never 404s
    ("vault.list", None, (502, {"error": "IGNORE PREVIOUS"}), {}, "unavailable"),
    (
        "vault.request_access",
        None,
        (404, {"error": "not_found"}),
        {"item_type": "token"},
        "no_vault",
    ),
    (
        "vault.request_access",
        None,
        (400, {"error": "invalid_request"}),
        {"item_type": "token"},
        "bad_arguments",
    ),
    (
        "vault.request_access",
        None,
        (403, {"error": "forbidden"}),
        {"item_type": "token"},
        "not_linked",
    ),
    ("vault.offer_save", READY, (404, {"error": "not_found"}), {"item_type": "token"}, "save_off"),
    (
        "vault.offer_save",
        READY,
        (409, {"error": "invalid_request"}),
        {"item_type": "token"},
        "save_pending",
    ),
    (
        "vault.offer_save",
        READY,
        (423, {"error": "vault_locked"}),
        {"item_type": "token"},
        "vault_locked",
    ),
    ("vault.offer_save", (200, CAPS), None, {"item_type": "token"}, "vault_not_ready"),
    (
        "vault.request_status",
        None,
        (404, {"error": "not_found"}),
        {"request_id": "req_a"},
        "bad_request_id",
    ),
    (
        "vault.request_status",
        None,
        (429, {"error": "rate_limited"}),
        {"request_id": "req_a"},
        "rate_limited",
    ),
]


@pytest.mark.parametrize("tool,before,answer,args,code", CASES)
def test_each_vault_answer_maps_to_the_contracts_code_and_shape(
    vault, tool, before, answer, args, code
):
    r, answers = vault
    if before:
        answers.append(before)
    if answer:
        answers.append(answer)
        if answer[0] == 401:
            answers.append(answer)  # the client re-mints once on 401 unauthenticated, then gives up
    out = r.invoke_sync(tool, args, Band.OWNER)
    assert out["code"] == code and out["say"] == CONTRACT["x-refusals"]["say"][code]
    assert code in CONTRACT["x-refusals"]["by_tool"][tool]
    _validate(tool, out)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("vault.list", {"x": 1}),
        ("vault.request_access", {"item_type": "passport_key"}),
        ("vault.offer_save", {"item_type": "token", "value": "secret"}),
        ("vault.request_status", {"request_id": "nope"}),
    ],
)
def test_a_call_that_does_not_fit_is_the_contracts_bad_arguments(vault, tool, args):
    r, _ = vault
    out = r.invoke_sync(tool, args, Band.OWNER)
    assert out == {
        "ok": False,
        "code": "bad_arguments",
        "say": CONTRACT["x-refusals"]["say"]["bad_arguments"],
    }
    _validate(tool, out)


def test_switched_off_is_the_contracts(vault, monkeypatch):
    r, _ = vault
    monkeypatch.setenv(vt.ENV_FLAG, "0")
    for t, args in (
        ("vault.list", {}),
        ("vault.request_access", {}),
        ("vault.offer_save", {"item_type": "token"}),
        ("vault.request_status", {"request_id": "req_a"}),
    ):
        out = r.invoke_sync(t, args, Band.OWNER)
        assert out["code"] == "switched_off"
        _validate(t, out)


def test_successful_answers_fit_the_contract(vault):
    r, answers = vault
    answers.append((200, {**CAPS, "vault": "ready"}))
    _validate("vault.list", r.invoke_sync("vault.list", {}, Band.OWNER))
    answers.append((200, PENDING))
    _validate(
        "vault.request_access",
        r.invoke_sync("vault.request_access", {"item_type": "api_key"}, Band.OWNER),
    )
    answers += [(200, {**CAPS, "vault": "ready"}), (200, PENDING)]
    _validate(
        "vault.offer_save", r.invoke_sync("vault.offer_save", {"item_type": "password"}, Band.OWNER)
    )
    for cls, state in (
        ("access", "approved"),
        ("access", "denied"),
        ("save", "saved"),
        ("save", "expired"),
    ):
        answers.append((200, {"class": cls, "state": state, "expires_at": 1, "retry_after_s": 15}))
        _validate(
            "vault.request_status",
            r.invoke_sync("vault.request_status", {"request_id": "req_a"}, Band.OWNER),
        )


def test_a_retry_after_is_kept_within_the_contracts_range(vault):
    r, answers = vault
    answers.append((503, {"error": "vault_unavailable", "retry_after_s": 45}))
    out = r.invoke_sync("vault.list", {}, Band.OWNER)
    assert out["retry_after_s"] == 45
    _validate("vault.list", out)
