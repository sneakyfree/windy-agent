"""Configured AND answering (Boss 10-10): a refused token's tools are not registered; a blip keeps them."""

from __future__ import annotations

import httpx
import pytest

from windyfly.agent.capabilities.credential_check import answers


def _t(status=None, exc=None):
    def handle(req):
        if exc:
            raise exc
        assert req.headers["Authorization"] == "Bearer s3cr3t-value"
        return httpx.Response(status, json={})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_token_does_not_answer(status, caplog):
    assert answers("github", "https://api.example/user", "s3cr3t-value", transport=_t(status)) is False
    assert "s3cr3t-value" not in caplog.text  # never the token


@pytest.mark.parametrize("status", [200, 429, 500, 503])
def test_anything_else_answers(status):
    assert answers("github", "https://api.example/user", "s3cr3t-value", transport=_t(status)) is True


def test_a_network_blip_keeps_the_tools():
    assert answers("github", "https://api.example/user", "s3cr3t-value", transport=_t(exc=httpx.ConnectTimeout("t"))) is True


def test_no_network_from_other_tests_by_default(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network")
    monkeypatch.setattr(httpx.Client, "get", boom)
    assert answers("github", "https://api.example/user", "tok") is True  # skipped under pytest


def test_github_with_a_refused_token_registers_nothing(monkeypatch):
    from windyfly.agent.capabilities import credential_check
    from windyfly.agent.capabilities.github import register_github_capabilities
    from windyfly.agent.capabilities.registry import CapabilityRegistry

    monkeypatch.setenv("GITHUB_PAT", "tok")
    monkeypatch.setattr(credential_check, "answers", lambda *a, **k: False)
    reg = CapabilityRegistry()
    register_github_capabilities(reg, {})
    assert reg.get("github.list_repo") is None


def test_cloudflare_with_a_refused_token_registers_nothing(monkeypatch):
    from windyfly.agent.capabilities import credential_check
    from windyfly.agent.capabilities.cloudflare import register_cloudflare_capabilities
    from windyfly.agent.capabilities.registry import CapabilityRegistry

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(credential_check, "answers", lambda *a, **k: False)
    reg = CapabilityRegistry()
    register_cloudflare_capabilities(reg, {})
    assert reg.get("cloudflare.list_zones") is None


def test_save_credential_says_plainly_when_the_tools_did_not_come_on(monkeypatch):
    from windyfly.agent.capabilities import credential_check, setup
    from windyfly.agent.capabilities.registry import CapabilityRegistry

    monkeypatch.setattr(setup, "_save_credential_handler",
                        lambda **kw: {"ok": True, "integration": kw["integration"], "hot_loaded": True})
    monkeypatch.setenv("GITHUB_PAT", "s3cr3t-value")
    reg = CapabilityRegistry()
    setup.register_setup_capabilities(reg, {})
    save = reg.get("setup.save_credential").handler

    monkeypatch.setattr(credential_check, "answers", lambda *a, **k: False)  # refused at registration
    out = save(integration="github", value="s3cr3t-value")
    assert out["tools_registered"] is False and "NOT available" in out["note_to_llm"]

    monkeypatch.setattr(credential_check, "answers", lambda *a, **k: True)
    out = save(integration="github", value="s3cr3t-value")
    assert out["tools_registered"] is True and reg.get("github.list_repo") is not None
