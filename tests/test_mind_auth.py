"""S10.8: Windy Mind calls carry an EPT+agent + a per-request DPoP proof.

Mind (windy-mind S4.6, live 10-04) takes ``Authorization: Bearer <EPT+agent>`` plus a
``DPoP:`` header on every non-GET; GETs are bearer-only. The legacy EPT stays as the
fallback until the sunset (non-GET refused from 11-03).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from windyfly.agent import mind_auth, models

URL = "https://api.windymind.ai/v1/chat"


def _resp(status=200, error=None, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"x-mind-error": error} if error else {}
    r.json.return_value = body or {}
    r.text = ""
    return r


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(mind_auth.ENV_FLAG, "1")
    monkeypatch.setattr(mind_auth, "_warned", set())
    from windyfly.eternitas import agent_keys as ak

    mints: list[str] = []
    proofs: list[tuple[str, str]] = []

    def fake_token(aud, **_kw):
        assert aud == "windy-mind"
        mints.append(aud)
        return {"token": f"agent-tok-{len(mints)}"}

    def fake_dpop(htm, url, **_kw):
        proofs.append((htm, url))
        return f"proof-{len(proofs)}"

    monkeypatch.setattr(ak, "request_agent_token", fake_token)
    monkeypatch.setattr(ak, "service_dpop", fake_dpop)
    monkeypatch.setattr(ak, "clear_token_cache", lambda: None)
    return mints, proofs


class TestHeaders:
    def test_post_gets_bearer_agent_token_and_dpop_for_that_request(self, _on):
        _, proofs = _on
        h, agent = mind_auth.headers("POST", URL, "legacy-ept")
        assert agent is True
        assert h == {"Authorization": "Bearer agent-tok-1", "DPoP": "proof-1"}
        assert proofs == [("POST", URL)]

    def test_get_is_bearer_only(self):
        h, agent = mind_auth.headers("GET", "https://api.windymind.ai/v1/grants/me", "legacy-ept")
        assert agent is True
        assert h == {"Authorization": "Bearer agent-tok-1"}

    def test_mint_failure_falls_back_to_the_legacy_ept(self, monkeypatch):
        from windyfly.eternitas import agent_keys as ak

        def boom(aud, **_kw):
            raise ak.AgentTokenError("no_key", "no registered agent key")

        monkeypatch.setattr(ak, "request_agent_token", boom)
        h, agent = mind_auth.headers("POST", URL, "legacy-ept")
        assert (h, agent) == ({"Authorization": "Bearer legacy-ept"}, False)

    def test_flag_off_means_legacy(self, monkeypatch):
        monkeypatch.setenv(mind_auth.ENV_FLAG, "0")
        assert mind_auth.headers("POST", URL, "legacy-ept") == ({"Authorization": "Bearer legacy-ept"}, False)

    def test_tests_never_mint_unless_they_opt_in(self, monkeypatch):
        monkeypatch.delenv(mind_auth.ENV_FLAG)
        assert mind_auth.enabled() is False  # PYTEST_CURRENT_TEST is set


class TestPost:
    def test_every_attempt_signs_a_fresh_proof(self, monkeypatch, _on):
        mints, proofs = _on
        sent = []

        def fake_post(url, headers, json, timeout):
            sent.append(headers)
            return _resp(401, "dpop_replay") if len(sent) == 1 else _resp(200)

        monkeypatch.setattr("httpx.post", fake_post)
        resp = mind_auth.post(URL, "legacy-ept", {"messages": []}, 30.0)
        assert resp.status_code == 200
        assert [h["DPoP"] for h in sent] == ["proof-1", "proof-2"]  # never the same proof twice
        assert [h["Authorization"] for h in sent] == ["Bearer agent-tok-1", "Bearer agent-tok-2"]
        assert all(h["Content-Type"] == "application/json" for h in sent)

    def test_refused_twice_falls_back_to_legacy(self, monkeypatch):
        sent = []

        def fake_post(url, headers, json, timeout):
            sent.append(headers)
            return _resp(401, "unknown_kid") if "DPoP" in headers else _resp(200)

        monkeypatch.setattr("httpx.post", fake_post)
        resp = mind_auth.post(URL, "legacy-ept", {}, 30.0)
        assert resp.status_code == 200
        assert len(sent) == 3 and sent[-1]["Authorization"] == "Bearer legacy-ept" and "DPoP" not in sent[-1]

    def test_other_answers_are_returned_untouched(self, monkeypatch):
        calls = []
        monkeypatch.setattr("httpx.post", lambda *a, **k: calls.append(1) or _resp(403, "grant_off"))
        assert mind_auth.post(URL, "legacy-ept", {}, 30.0).status_code == 403
        assert len(calls) == 1  # a stop is not an auth failure: no retry

    def test_ordinary_401_is_not_retried(self, monkeypatch):
        calls = []
        monkeypatch.setattr("httpx.post", lambda *a, **k: calls.append(1) or _resp(401, "not_authenticated"))
        mind_auth.post(URL, "legacy-ept", {}, 30.0)
        assert len(calls) == 1


class TestWiredIntoTheBrokerPath:
    def test_chat_goes_out_as_ept_agent_with_dpop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "legacy-ept")
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
        seen = []

        def fake_post(url, headers, json, timeout):
            seen.append((url, headers))
            return _resp(200, body={
                "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        monkeypatch.setattr("httpx.post", fake_post)
        out = models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        assert out and out["content"] == "hi"
        url, headers = seen[0]
        assert url.endswith("/v1/chat")
        assert headers["Authorization"] == "Bearer agent-tok-1"
        assert headers["DPoP"] == "proof-1"


class TestClientPost:
    """Runtime claim / heartbeat / release go through an httpx.Client on Mind's base URL."""

    def _client(self, handler):
        import httpx

        return httpx.Client(base_url="https://api.windymind.ai", transport=httpx.MockTransport(handler),
                            headers={"Authorization": "Bearer legacy-ept"})

    def test_heartbeat_is_ept_agent_with_a_proof_for_its_own_url(self, _on):
        _, proofs = _on
        seen = []

        def handler(request):
            import httpx

            seen.append(request.headers)
            return httpx.Response(200, json={"ok": True})

        with self._client(handler) as c:
            r = mind_auth.client_post(c, "/v1/runtime/heartbeat", "legacy-ept", {"passport": "P"})
        assert r.status_code == 200
        assert seen[0]["authorization"] == "Bearer agent-tok-1"  # per-request header beats the client default
        assert seen[0]["dpop"] == "proof-1"
        assert proofs == [("POST", "https://api.windymind.ai/v1/runtime/heartbeat")]

    def test_refused_proof_is_resigned_then_legacy(self):
        import httpx

        seen = []

        def handler(request):
            seen.append(dict(request.headers))
            if "dpop" in request.headers:
                return httpx.Response(401, headers={"x-mind-error": "dpop_replay"})
            return httpx.Response(200)

        with self._client(handler) as c:
            r = mind_auth.client_post(c, "/v1/runtime/release", "legacy-ept", {})
        assert r.status_code == 200
        assert [h.get("dpop") for h in seen] == ["proof-1", "proof-2", None]
        assert seen[-1]["authorization"] == "Bearer legacy-ept"

    def test_flag_off_keeps_the_legacy_bearer(self, monkeypatch):
        import httpx

        monkeypatch.setenv(mind_auth.ENV_FLAG, "0")
        seen = []

        def handler(request):
            seen.append(request.headers)
            return httpx.Response(200)

        with self._client(handler) as c:
            mind_auth.client_post(c, "/v1/runtime/claim", "legacy-ept", {})
        assert seen[0]["authorization"] == "Bearer legacy-ept" and "dpop" not in seen[0]
