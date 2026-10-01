"""model = "auto": send Mind no model; direct providers never see the word."""
from windyfly.agent import models


def test_is_auto_model():
    assert models.is_auto_model("auto") and models.is_auto_model(" AUTO ")
    assert not models.is_auto_model(None) and not models.is_auto_model("") and not models.is_auto_model("claude-sonnet-5")


def test_chain_has_no_auto_entry():
    assert models._build_chain(None, {"agent": {"default_model": "auto"}}) == []
    assert models._build_chain(None, {"agent": {"failover_chain": ["auto", "gpt-4o-mini"]}}) == ["gpt-4o-mini"]
    assert models._build_chain(None, {"agent": {"default_model": "claude-sonnet-5"}}) == ["claude-sonnet-5"]


def test_mind_gets_no_model_when_auto(monkeypatch):
    seen = {}

    def fake(messages, model, temperature, max_tokens, tools):
        seen["model"] = model
        return {"content": "hi", "input_tokens": 1, "output_tokens": 1, "mind_model": "claude-sonnet-5-5"}

    monkeypatch.setattr(models, "_try_mind_broker", fake)
    out = models.call_llm([{"role": "user", "content": "x"}], model="auto", config={"agent": {"default_model": "auto"}})
    assert seen["model"] is None
    assert out["content"] == "hi"
