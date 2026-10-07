"""`windy model test` must not send a direct provider call for a passport agent (Boss 10-07)."""

from unittest.mock import patch

from windyfly.commands import _legacy


def test_passport_agent_does_not_call_a_provider(monkeypatch, capsys):
    monkeypatch.setenv("DEFAULT_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test")
    with patch("httpx.post") as post:
        _legacy._model_test()
    post.assert_not_called()
    assert "Windy Mind" in capsys.readouterr().out


def test_standalone_install_still_tests_its_own_key(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    with patch("httpx.post") as post:
        post.return_value.json.return_value = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        _legacy._model_test()
    post.assert_called_once()
