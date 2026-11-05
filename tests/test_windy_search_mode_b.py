"""Fly -> Windy Search mode B (EPT+agent aud windy-search + DPoP), DARK behind WINDY_SEARCH_EPT_AGENT.

Built to Windy Search's answers of 11-04 (windy-search #129 @ acd60a1e, #130 86d13fa4): refusals are 401
{"detail": "<code>"}; only "mode B is off" falls back to the stored EPT; a render on mode B is a 502 capability gap.
"""
from __future__ import annotations

import logging

import httpx
import pytest

import windyfly.tools.windy_search_client as client
from windyfly.agent import service_auth

STORED = "stored-ept-value"
SEARCH = "https://api.windysearch.com/v1/search"
FETCH = "https://api.windysearch.com/web/fetch"


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", STORED)
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
    monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
    monkeypatch.setenv(client.SEARCH_FLAG, "1")
    monkeypatch.setattr(client, "_budget_exhausted_until", 0.0, raising=False)
    state = {"answers": [], "sent": [], "mints": 0, "proofs": 0, "forgot": 0, "mint_fails": False}

    def agent_headers(aud, method, url):
        if state["mint_fails"]:
            raise service_auth.ServiceAuthError("agent_paused", "")
        if state["forgot"] >= state["mints"]:
            state["mints"] += 1
        state["proofs"] += 1
        state["calls"] = state.get("calls", []) + [(aud, method, url)]
        return {"Authorization": f"Bearer agent-token-{state['mints']}", "DPoP": f"proof-{state['proofs']}"}

    def forget():
        state["forgot"] += 1

    monkeypatch.setattr(service_auth, "agent_headers", agent_headers)
    monkeypatch.setattr(service_auth, "forget_token", forget)

    def fake_post(url, **kw):
        h = kw.get("headers") or {}
        state["sent"].append({"url": url, "auth": h.get("Authorization", ""), "dpop": h.get("DPoP")})
        status, body = state["answers"].pop(0) if state["answers"] else (200, {"results": [], "content": "ok"})
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(client.httpx, "post", fake_post)
    return state


def _kinds(state):
    return ["stored" if s["auth"] == f"Bearer {STORED}" else ("mode-b" if s["dpop"] else "none")
            for s in state["sent"]]


def test_flag_off_sends_the_stored_ept_only(box, monkeypatch):
    monkeypatch.delenv(client.SEARCH_FLAG)
    client.search_via_windy_search("q")
    assert _kinds(box) == ["stored"] and box["mints"] == 0


def test_flag_on_sends_aud_windy_search_and_a_proof_bound_to_the_exact_url(box):
    client.search_via_windy_search("q")
    client.fetch_via_windy_search("https://example.com", render="off")
    assert _kinds(box) == ["mode-b", "mode-b"]
    assert box["calls"] == [("windy-search", "POST", SEARCH), ("windy-search", "POST", FETCH)]  # no trailing slash


@pytest.mark.parametrize("detail", ["wrong_token_type", "Bearer EPT required in Authorization header"])
def test_search_mode_b_off_falls_back_at_once_without_a_re_mint(box, detail):
    box["answers"] = [(401, {"detail": detail})]
    out = client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "stored"] and box["forgot"] == 0 and "error" not in out


@pytest.mark.parametrize("detail", ["wrong_audience", "invalid_token", "expired", "mode_b_required"])
def test_a_new_token_code_re_mints_once(box, detail):
    box["answers"] = [(401, {"detail": detail})]
    client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "mode-b"] and box["forgot"] == 1
    assert box["sent"][0]["auth"] != box["sent"][1]["auth"]


@pytest.mark.parametrize("detail", ["dpop_required", "invalid_dpop", "dpop_replay"])
def test_a_proof_code_retries_once_with_a_fresh_proof_same_token(box, detail):
    box["answers"] = [(401, {"detail": detail})]
    client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "mode-b"] and box["forgot"] == 0
    assert box["sent"][0]["auth"] == box["sent"][1]["auth"] and box["sent"][0]["dpop"] != box["sent"][1]["dpop"]


def test_every_send_carries_a_new_proof(box):
    for _ in range(3):
        client.search_via_windy_search("q")
    assert len({s["dpop"] for s in box["sent"]}) == 3


@pytest.mark.parametrize("status,detail", [
    (401, "EPT revoked"), (401, "EPT suspended"), (401, "ept_before_cutoff"), (401, "agent_paused"),
    (401, "something new"), (401, None),
    (503, "Revocation status unavailable — retry shortly"), (503, "Suspension status unavailable — retry shortly"),
    (503, "Revocation checking not configured"), (503, "agent_pause_state_unavailable"),
    (409, "byo blocked"), (429, "slow down"),
])
def test_never_falls_back_on_anything_else(box, status, detail):
    box["answers"] = [(status, {"detail": detail} if detail is not None else {})]
    out = client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b"] and out["provider"] == "windy-search-error"


def test_a_retry_still_refused_with_a_non_fallback_code_stands(box):
    box["answers"] = [(401, {"detail": "expired"}), (401, {"detail": "agent_paused"})]
    out = client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "mode-b"] and "error" in out


def test_a_retry_answered_mode_b_off_then_falls_back_once(box):
    box["answers"] = [(401, {"detail": "invalid_dpop"}), (401, {"detail": "wrong_token_type"})]
    client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "mode-b", "stored"]


def test_a_fallback_answered_mode_b_required_is_final(box):
    box["answers"] = [(401, {"detail": "wrong_token_type"}), (401, {"detail": "mode_b_required"})]
    out = client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b", "stored"] and "error" in out  # no loop back to mode B


def test_no_stored_ept_means_no_fallback_send(box, monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    box["answers"] = [(401, {"detail": "wrong_token_type"})]
    client.search_via_windy_search("q")
    assert _kinds(box) == ["mode-b"]


def test_no_mint_uses_the_stored_ept(box):
    box["mint_fails"] = True
    client.search_via_windy_search("q")
    assert _kinds(box) == ["stored"]


@pytest.mark.parametrize("render", ["on", "auto"])
def test_a_mode_b_render_gap_is_sent_once_with_the_stored_ept(box, render):
    box["answers"] = [(502, {"detail": "render failed: no acting passport"})]
    out = client.fetch_via_windy_search("https://example.com", render=render)
    assert _kinds(box) == ["mode-b", "stored"] and out["content"] == "ok"


def test_a_502_on_a_plain_fetch_or_another_502_never_falls_back(box):
    box["answers"] = [(502, {"detail": "render failed: x"}), (502, {"detail": "upstream"})]
    client.fetch_via_windy_search("https://example.com", render="off")
    client.fetch_via_windy_search("https://example.com", render="on")
    assert _kinds(box) == ["mode-b", "mode-b"]


def test_logs_carry_codes_never_tokens(box, caplog):
    box["answers"] = [(401, {"detail": "wrong_token_type"})]
    with caplog.at_level(logging.INFO, logger=client.__name__):
        client.search_via_windy_search("q")
    text = caplog.text
    assert "[search-auth]" in text and "wrong_token_type" in text
    assert STORED not in text and "agent-token" not in text and "proof-" not in text


# ── Search's flip list (11-04): one INFO line at startup naming the base the proofs are made for ──────────────

@pytest.mark.parametrize("base, words", [(None, "(the default)"), ("https://search.example/", "NOT the default")])
def test_startup_names_the_search_base_once_with_mode_b_on(box, monkeypatch, caplog, base, words):
    if base:
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", base)
    with caplog.at_level(logging.INFO, logger=client.__name__):
        client.log_mode_b_base()
    lines = [r.getMessage() for r in caplog.records if "Windy Search base" in r.getMessage()]
    assert len(lines) == 1 and words in lines[0] and (base or client.DEFAULT_BASE_URL).rstrip("/") in lines[0]


def test_startup_says_nothing_with_mode_b_off(box, monkeypatch, caplog):
    monkeypatch.delenv(client.SEARCH_FLAG)
    with caplog.at_level(logging.DEBUG, logger=client.__name__):
        client.log_mode_b_base()
    assert caplog.text == ""


def test_main_logs_the_search_base_at_startup():
    import ast
    import inspect

    from windyfly import main

    calls = [n for n in ast.walk(ast.parse(inspect.getsource(main.main)))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "log_mode_b_base"]
    assert len(calls) == 1
