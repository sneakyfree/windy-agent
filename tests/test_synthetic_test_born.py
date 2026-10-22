"""A test-born agent's own traffic is synthetic (10-22: the lab agent's Mind calls went out unmarked, ~10 a day).

synthetic.active() is true with WINDY_SYNTHETIC=1 (probes) OR when the agent's own passport number starts with
exactly "ET26-TEST-" (case-sensitive, full prefix). A real ET26 passport, an empty or missing value never is.
"""

from __future__ import annotations

import pytest

from windyfly.observability import synthetic


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("WINDY_SYNTHETIC", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)


@pytest.mark.parametrize("passport", ["ET26-TEST-M0NF", "ET26-TEST-66QC", " ET26-TEST-JRDZ "])
def test_a_test_born_passport_is_synthetic(monkeypatch, passport):
    monkeypatch.setenv("ETERNITAS_PASSPORT", passport)
    assert synthetic.active() is True


@pytest.mark.parametrize("passport", ["ET26-T11V-NPD1", "ET26-TESTX-0001", "et26-test-m0nf", "ET26-TEST", "", "   "])
def test_a_real_or_odd_passport_is_not(monkeypatch, passport):
    monkeypatch.setenv("ETERNITAS_PASSPORT", passport)
    assert synthetic.active() is False


def test_missing_env_is_not():
    assert synthetic.active() is False


def test_the_probe_switch_still_works(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    assert synthetic.active() is True


def test_a_test_born_agent_marks_windy_hosts_only(monkeypatch):
    """With the wrapper on, a request to a Windy host carries the header; a third party never does."""
    import httpx

    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-M0NF")
    monkeypatch.setattr(synthetic, "_installed", False)
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)        # restored after the test
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)
    assert synthetic.install() is True
    seen = {}

    def handler(request):
        seen[request.url.host] = request.headers.get(synthetic.HEADER)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get("https://api.windymind.ai/v1/x")
        c.get("https://api.anthropic.com/v1/x")
    assert seen == {"api.windymind.ai": "1", "api.anthropic.com": None}
