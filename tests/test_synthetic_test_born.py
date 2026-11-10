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
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)


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


def test_a_real_passport_is_never_synthetic_even_with_the_probe_switch(monkeypatch):
    """Hub + Boss 11-04: Zero (a real agent) went out marked synthetic on ~165 Mind calls a week. Never again."""
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    assert synthetic.active() is False


def test_a_real_passport_in_the_token_alone_is_never_synthetic(monkeypatch):
    import base64
    import json

    claims = base64.urlsafe_b64encode(json.dumps({"sub": "ET26-T11V-NPD1"}).encode()).rstrip(b"=").decode()
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", f"e30.{claims}.sig")
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    assert synthetic.active() is False


def test_the_probe_switch_still_marks_a_process_with_no_passport(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    assert synthetic.active() is True


def test_a_real_passport_never_sends_the_header_even_after_the_wrapper_went_on(monkeypatch):
    """The wrapper may go on before the .env loads a real passport: each request checks again."""
    import httpx

    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")                          # a probe switch, no passport yet
    monkeypatch.setattr(synthetic, "_installed", False)
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)
    assert synthetic.install() is True
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")          # then the real agent's passport loads
    seen = []

    def handler(request):
        seen.append(request.headers.get(synthetic.HEADER))
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get("https://api.windymind.ai/v1/chat")
    assert seen == [None]


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
    # conftest stubs load_dotenv suite-wide; here the agent's env loader gets the real one (it only ever reads a NAMED
    # file: WINDY_ENV_FILE, or the project .env when this agent's config is in the project, as here).
    from dotenv.main import load_dotenv as real_load_dotenv

    import windyfly.env_file as env_file

    monkeypatch.setattr(env_file, "load_dotenv", real_load_dotenv)
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["windyfly.main"])                # default --config windyfly.toml, in the project

    class _Stop(Exception):
        pass

    def stop(*a, **k):
        raise _Stop

    monkeypatch.setattr(wmain, "load_config", stop)                   # boot stops right after the .env + install
    assert synthetic.active() is False
    with pytest.raises(_Stop):
        wmain.main()
    assert synthetic.active() is True and synthetic._installed is True


def test_a_header_set_by_hand_never_goes_out_with_a_real_passport(monkeypatch):
    """Hub on #710: the wrapper also STRIPS the header for a real passport, whoever set it."""
    import httpx

    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
    monkeypatch.setattr(synthetic, "_installed", False)
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)
    assert synthetic.install() is True                                  # on for a real passport too
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get(synthetic.HEADER)))
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get("https://api.windymind.ai/v1/chat", headers={synthetic.HEADER: "1"})
        c.get("https://example.com/x", headers={synthetic.HEADER: "1"})   # not a Windy host: left alone
    assert seen == [("api.windymind.ai", None), ("example.com", "1")]


def test_no_passport_and_no_switch_installs_nothing(monkeypatch):
    monkeypatch.setattr(synthetic, "_installed", False)
    assert synthetic.install() is False
