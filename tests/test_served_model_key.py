"""The model Mind served is surfaced for Matrix replies, and only that."""
from windyfly.agent.loop import _note_served_model, pop_served_model


def test_served_model_roundtrip_and_clear():
    _note_served_model("s1", "claude-sonnet-5")
    assert pop_served_model("s1") == "claude-sonnet-5"
    assert pop_served_model("s1") is None


def test_missing_model_clears_stale_value():
    _note_served_model("s2", "claude-opus-5-5")
    _note_served_model("s2", None)
    assert pop_served_model("s2") is None
