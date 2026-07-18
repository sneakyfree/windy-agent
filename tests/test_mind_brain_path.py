"""Mind-brain path hardening (Sprint 5, keyless grandma sprint).

The 2026-07-04 Mind audit found the Fly→Mind pipe was wired but never
load-bearing: PR #173 returned Mind's near-OpenAI JSON verbatim (the
loop expects the flat windyfly shape), ETERNITAS_PASSPORT_TOKEN had no
producer, failures were one silent info-level attempt per call, and
Mind has no tool support yet (tools would be silently dropped).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import models


MIND_JSON = {
    "id": "req-1",
    "model": "gemini-2.5-flash",
    "provider": "google",
    "choices": [
        {
            "message": {"role": "assistant", "content": "Hello from Mind!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28},
}


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


@pytest.fixture(autouse=True)
def _mind_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test-token")
    monkeypatch.delenv("WINDY_MIND_SEND_TOOLS", raising=False)
    monkeypatch.setattr(models, "_provider_cooldowns", {})
    yield


class TestResponseTranslation:
    def test_mind_openai_shape_translates_to_flat(self):
        out = models._translate_mind_response(_resp(MIND_JSON))
        assert out["content"] == "Hello from Mind!"
        assert out["input_tokens"] == 21
        assert out["output_tokens"] == 7
        assert out["tool_calls"] is None
        assert out["mind_provider"] == "google"

    def test_flat_shape_passthrough(self):
        flat = {"content": "hi", "input_tokens": 3, "output_tokens": 1}
        out = models._translate_mind_response(_resp(flat))
        assert out["content"] == "hi"
        assert out["input_tokens"] == 3

    def test_garbage_shapes_return_none(self):
        assert models._translate_mind_response(_resp({"choices": []})) is None
        assert models._translate_mind_response(_resp({"nope": 1})) is None
        assert models._translate_mind_response(_resp(["list"])) is None


class TestBrokerResilience:
    def _call(self):
        return models._try_mind_broker(
            [{"role": "user", "content": "hi"}], None, 0.7, 1024, None,
        )

    def test_success_returns_translated(self):
        with patch("httpx.post", return_value=_resp(MIND_JSON)):
            out = self._call()
        assert out["content"] == "Hello from Mind!"
        assert "windy-mind" not in models._provider_cooldowns

    def test_http_failure_records_cooldown_and_skips_next(self):
        with patch("httpx.post", return_value=_resp({"detail": "boom"}, 503)):
            assert self._call() is None
        assert "windy-mind" in models._provider_cooldowns
        # While cooling, the broker must not even attempt the network.
        with patch("httpx.post") as mock_post:
            assert self._call() is None
            mock_post.assert_not_called()

    def test_network_error_records_cooldown(self):
        with patch("httpx.post", side_effect=OSError("dns")):
            assert self._call() is None
        assert "windy-mind" in models._provider_cooldowns

    def test_invalid_shape_records_cooldown(self):
        with patch("httpx.post", return_value=_resp({"weird": True})):
            assert self._call() is None
        assert "windy-mind" in models._provider_cooldowns

    def test_tools_flow_to_mind_by_default(self, monkeypatch):
        """Default flipped 2026-07-05 (0c2 drill finding): Mind tools
        shipped+verified live, so tool-bearing calls now go to Mind
        unless explicitly opted out with WINDY_MIND_SEND_TOOLS=0."""
        tools = [{"type": "function", "function": {"name": "fs_read"}}]
        with patch("httpx.post", return_value=_resp(MIND_JSON)) as mock_post:
            out = models._try_mind_broker(
                [{"role": "user", "content": "hi"}], None, 0.7, 1024, tools,
            )
            assert out is not None
            assert mock_post.call_args.kwargs["json"]["tools"] == tools

    def test_tools_opt_out_restores_skip(self, monkeypatch):
        monkeypatch.setenv("WINDY_MIND_SEND_TOOLS", "0")
        tools = [{"type": "function", "function": {"name": "fs_read"}}]
        with patch("httpx.post") as mock_post:
            out = models._try_mind_broker(
                [{"role": "user", "content": "hi"}], None, 0.7, 1024, tools,
            )
            assert out is None
            mock_post.assert_not_called()

    def test_no_ept_is_noop(self, monkeypatch):
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        with patch("httpx.post") as mock_post:
            assert self._call() is None
            mock_post.assert_not_called()


class TestMindStatus:
    def test_status_reflects_configuration_and_cooldown(self):
        with patch.object(models, "_max_oauth_active", return_value=False):
            st = models.mind_broker_status()
            assert st["configured"] is True  # EPT set by fixture
            models._record_provider_failure("windy-mind", "503")
            st = models.mind_broker_status()
            assert st["in_cooldown"] is True
            assert st["cooldown_remaining_s"] > 0

    def test_status_unconfigured_without_ept(self, monkeypatch):
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        assert models.mind_broker_status()["configured"] is False


class TestHatchEptPersistence:
    def test_step_eternitas_captures_and_persists_ept(
        self, monkeypatch, tmp_path,
    ):
        import asyncio

        from windyfly import hatch_orchestrator as ho

        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=1\n")
        monkeypatch.setattr(
            "windyfly.platform.get_project_root", lambda: tmp_path,
        )
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)

        passport = MagicMock()
        passport.passport_id = "ET26-TEST-0001"
        passport.status = "active"
        passport.ept_token = "ept-jwt-abc123"

        client = MagicMock()
        client.register = MagicMock(return_value=_async_return(passport))

        with patch(
            "windyfly.eternitas.provision.get_eternitas_client",
            return_value=client,
        ):
            result = ho.HatchResult()
            asyncio.run(ho._step_eternitas(result, "Testy", "o1", "Owner", db=None))

        assert result.passport_id == "ET26-TEST-0001"
        import os
        try:
            assert os.environ["ETERNITAS_PASSPORT_TOKEN"] == "ept-jwt-abc123"
            content = env_file.read_text(encoding="utf-8")
            assert "ETERNITAS_PASSPORT_TOKEN=ept-jwt-abc123" in content
            assert "EXISTING=1" in content
        finally:
            # production code set this — don't leak into other tests
            os.environ.pop("ETERNITAS_PASSPORT_TOKEN", None)

    def test_missing_ept_token_is_not_fatal(self, monkeypatch, tmp_path):
        import asyncio

        from windyfly import hatch_orchestrator as ho

        monkeypatch.setattr(
            "windyfly.platform.get_project_root", lambda: tmp_path,
        )
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        passport = MagicMock()
        passport.passport_id = "ET26-TEST-0002"
        passport.status = "active"
        passport.ept_token = ""
        client = MagicMock()
        client.register = MagicMock(return_value=_async_return(passport))
        with patch(
            "windyfly.eternitas.provision.get_eternitas_client",
            return_value=client,
        ):
            result = ho.HatchResult()
            asyncio.run(ho._step_eternitas(result, "T", "o", "O", db=None))
        assert result.passport_id == "ET26-TEST-0002"
        import os
        assert "ETERNITAS_PASSPORT_TOKEN" not in os.environ


def _async_return(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro()
