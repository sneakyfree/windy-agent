"""mind_self + the mind.* capabilities (plan v2.1 S18.4): the agent's own model switch in Windy Mind."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from windyfly.agent import mind_self
from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.mind_model import register_mind_model_capabilities
from windyfly.agent.capabilities.registry import CapabilityRegistry

MODELS = ["claude-haiku-4-5", "claude-sonnet-5-5", "groq/gpt-oss-120b", "groq/openai/gpt-oss-20b", "grok-4"]


def _self(**kw):
    base = {"v": 1, "identity": {}, "default_model": "claude-sonnet-5-5", "chain": [], "source": "inherit",
            "state": "on", "state_reason": None, "effective": {"model": "claude-sonnet-5-5", "provider": "anthropic"},
            "burn": {"window_hours": 24, "calls": 3, "tokens_in": 100, "tokens_out": 20, "cost_usd": 0.5},
            "may_pick": {"mode": "free_and_own", "models": MODELS, "source": "owner"}, "picked_by": None,
            "config_version": 4}
    base.update(kw)
    return base


def _resp(status=200, body=None, etag='W/"4"'):
    r = MagicMock()
    r.status_code = status
    r.headers = {"etag": etag} if etag else {}
    r.json.return_value = body if body is not None else {}
    return r


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(mind_self.ENV_FLAG, "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
    monkeypatch.setenv("WINDY_MIND_EPT_AGENT", "0")  # legacy bearer: no real mint
    mind_self._reset_for_tests()
    calls: list[tuple] = []
    monkeypatch.setattr("httpx.request", lambda method, url, **kw: calls.append((method, url, kw)) or _resp(body=_self()))
    return calls


def _answer(monkeypatch, calls, *responses):
    seq = list(responses)

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr("httpx.request", fake)


class TestReading:
    def test_picked_model_only_when_someone_picked(self, monkeypatch, _on):
        assert mind_self.picked_model() is None
        mind_self._reset_for_tests()
        _answer(monkeypatch, _on, _resp(body=_self(picked_by="agent", effective={"model": "grok-4"})))
        assert mind_self.picked_model() == "grok-4"

    def test_etag_is_sent_and_a_304_keeps_the_cache(self, monkeypatch, _on):
        mind_self.fetch(force=True)
        _answer(monkeypatch, _on, _resp(304))
        s = mind_self.fetch(force=True)
        assert s and s["config_version"] == 4
        assert _on[-1][2]["headers"]["If-None-Match"] == 'W/"4"'

    def test_404_switches_quietly_off_for_a_while(self, monkeypatch, _on):
        _answer(monkeypatch, _on, _resp(404, {"error": {"code": "not_found"}}))
        assert mind_self.fetch(force=True) is None
        n = len(_on)
        assert mind_self.fetch(force=True) is None and len(_on) == n  # no second call

    def test_a_down_mind_costs_one_try_per_refresh_window(self, monkeypatch, _on):
        def boom(method, url, **kw):
            _on.append((method, url, kw))
            raise OSError("down")

        monkeypatch.setattr("httpx.request", boom)
        mind_self.fetch(force=True)
        n = len(_on)
        mind_self.picked_model()
        assert len(_on) == n

    def test_usage_has_no_money(self):
        out = mind_self.my_usage()
        assert out["calls"] == 3 and "cost" not in json.dumps(out)

    def test_why_paused_in_plain_words(self, monkeypatch, _on):
        assert mind_self.why_paused()["paused"] is False
        mind_self._reset_for_tests()
        _answer(monkeypatch, _on, _resp(body=_self(state="off", state_reason="grant_off")))
        out = mind_self.why_paused()
        assert out["paused"] and "owner switched me off" in out["say"]


class TestSwitching:
    def test_owner_has_not_opted_in(self, monkeypatch, _on):
        _answer(monkeypatch, _on, _resp(body=_self(may_pick={"mode": "none", "models": []})))
        out = mind_self.switch_model("groq")
        assert out["ok"] is False and "Models my helpers may pick" in out["say"]
        assert not [c for c in _on if c[0] == "PUT"]

    def test_words_that_are_not_an_exact_id_do_not_switch_and_list_the_choices(self, _on):
        for words in ("groq", "Claude Haiku", "gpt-5", ""):
            out = mind_self.switch_model(words)
            assert out["ok"] is False and out["allowed"] == MODELS and "can pick from" in out["say"]
        assert not [c for c in _on if c[0] == "PUT"]

    def test_exact_id_matches_case_insensitively(self, monkeypatch, _on):
        _answer(monkeypatch, _on, _resp(body=_self()), _resp(body=_self(picked_by="agent"), etag='W/"5"'))
        mind_self.switch_model("  GROK-4 ")
        assert [c for c in _on if c[0] == "PUT"][0][2]["json"] == {"model": "grok-4"}

    def test_success_puts_the_exact_id_and_clears_channel_pins(self, monkeypatch, _on):
        cleared = []
        monkeypatch.setattr("windyfly.agent.session_reset.clear_all_models", lambda: cleared.append(1) or 1)
        _answer(monkeypatch, _on, _resp(body=_self()),
                _resp(body=_self(picked_by="agent", effective={"model": "grok-4"}), etag='W/"5"'))
        out = mind_self.switch_model("grok-4")
        assert out["ok"] and out["model"] == "grok-4"
        put = [c for c in _on if c[0] == "PUT"][0]
        assert put[1].endswith("/v1/agents/me/model") and put[2]["json"] == {"model": "grok-4"}
        assert cleared == [1]
        assert mind_self.picked_model() == "grok-4"  # the cache already holds the new pick

    def test_mind_refusals_in_plain_words(self, monkeypatch, _on):
        _answer(monkeypatch, _on, _resp(body=_self()),
                _resp(403, {"error": {"code": "not_allowed_for_agent", "allowed": ["grok-4"]}}))
        assert "grok-4" in mind_self.switch_model("grok-4")["say"]
        mind_self._reset_for_tests()
        _answer(monkeypatch, _on, _resp(body=_self()), _resp(429, {"error": {"code": "switch_rate", "retry_after_s": 1800}}))
        assert "30 minutes" in mind_self.switch_model("grok-4")["say"]

    def test_reset_deletes(self, monkeypatch, _on):
        _answer(monkeypatch, _on, _resp(body=_self(picked_by=None)))
        out = mind_self.reset_model()
        assert out["ok"] and [c for c in _on if c[0] == "DELETE"]


class TestCapabilities:
    def test_bands_reads_user_changes_owner(self):
        reg = CapabilityRegistry()
        register_mind_model_capabilities(reg)
        caps = {c.id: c for c in reg.all()}
        assert set(caps) == {"mind.status", "mind.list_models", "mind.switch_model"}  # tool trim 10-10
        assert caps["mind.status"].band_required == Band.USER
        assert caps["mind.switch_model"].band_required == Band.OWNER

    def test_status_carries_usage_and_pause_and_reset_is_a_switch(self, monkeypatch):
        monkeypatch.setattr(mind_self, "status", lambda: {"model": "m"})
        monkeypatch.setattr(mind_self, "my_usage", lambda: {"calls": 1})
        monkeypatch.setattr(mind_self, "why_paused", lambda: {"paused": False})
        monkeypatch.setattr(mind_self, "reset_model", lambda: {"ok": True, "reset": True})
        monkeypatch.setattr(mind_self, "switch_model", lambda m: {"ok": True, "model": m})
        reg = CapabilityRegistry()
        register_mind_model_capabilities(reg)
        caps = {c.id: c for c in reg.all()}
        assert caps["mind.status"].handler() == {"model": "m", "usage": {"calls": 1}, "paused": {"paused": False}}
        assert caps["mind.switch_model"].handler(model="reset") == {"ok": True, "reset": True}
        assert caps["mind.switch_model"].handler(model="x-1") == {"ok": True, "model": "x-1"}

    def test_flag_off_registers_nothing(self, monkeypatch):
        monkeypatch.setenv(mind_self.ENV_FLAG, "0")
        reg = CapabilityRegistry()
        register_mind_model_capabilities(reg)
        assert reg.count() == 0
