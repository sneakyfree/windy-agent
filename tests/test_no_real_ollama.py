"""The conftest guard: no test reaches a real Ollama on localhost:11434."""
import httpx
import pytest


def test_sync_request_to_ollama_is_refused():
    with pytest.raises(httpx.ConnectError):
        httpx.post("http://localhost:11434/api/generate", json={"model": "x"}, timeout=1)


def test_warmup_reports_failure_instead_of_calling_ollama():
    from windyfly.agent import offline

    assert offline.warm_ollama_model("llama3.2:3b") is False
