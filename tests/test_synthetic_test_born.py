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


def test_a_passport_only_in_the_agents_env_file_is_seen_at_boot(monkeypatch, tmp_path):
    """windy bring-home writes ETERNITAS_PASSPORT into the agent's .env, not the process env: main() loads it and then
    installs the marking (the CLI's earlier install() ran before the .env was read). Hub on #647."""
    import httpx

    from windyfly import main as wmain

    (tmp_path / ".env").write_text("ETERNITAS_PASSPORT=ET26-TEST-BH01\n", encoding="utf-8")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "placeholder")          # so teardown restores "absent" ...
    monkeypatch.delenv("ETERNITAS_PASSPORT")                         # ... after load_dotenv sets it
    monkeypatch.setattr(synthetic, "_installed", False)
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)
    monkeypatch.setattr("windyfly.platform.get_project_root", lambda: tmp_path)
    # conftest stubs load_dotenv suite-wide; here main() gets the real one, for an EXPLICIT path only (the bare
    # upward search stays off, so no real .env can leak in).
    from dotenv.main import load_dotenv as real_load_dotenv

    monkeypatch.setattr(wmain, "load_dotenv", lambda path=None, **k: real_load_dotenv(path) if path else False)
    monkeypatch.chdir(tmp_path)

    class _Stop(Exception):
        pass

    def stop(*a, **k):
        raise _Stop

    monkeypatch.setattr(wmain.argparse, "ArgumentParser", stop)      # boot stops right after the .env + install
    assert synthetic.active() is False
    with pytest.raises(_Stop):
        wmain.main()
    assert synthetic.active() is True and synthetic._installed is True
