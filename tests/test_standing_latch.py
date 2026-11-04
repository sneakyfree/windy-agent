"""Eternitas refuses this agent's token => the agent STOPS (Boss 11-04; Hub's windy-contracts #265 Q4; home 1.1 H13a).

A refused mint (retired, suspended, revoked, or this computer's key revoked) latches a stop: no model call of any kind
(Mind, a provider, the local lifeboat), no tool, nothing outward, and one plain line for a person. It survives a
restart, and only the next GOOD mint lifts it. A 60 s probe keeps it current while the agent runs."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import models, standing  # noqa: E402
from windyfly.eternitas import agent_keys as ak  # noqa: E402

PASSPORT = "ET26-STND-0001"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "MIND_API_URL"):
        monkeypatch.delenv(k, raising=False)
    standing._cache.update(mtime=None, value=None)
    ak._TOKEN_CACHE.clear()
    yield
    standing.stop_probe()
    ak._TOKEN_CACHE.clear()


def _registered_key() -> str:
    kid = ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-01T00:00:00Z", passport=PASSPORT)
    ak.save_credentials(data)
    return kid


def _jwt(claims: dict) -> str:
    import base64

    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'ES256', 'typ': 'EPT+agent'})}.{enc(claims)}.SIG"


def _eternitas(answers: list[tuple[int, dict]]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        status, body = answers.pop(0)
        if status == 200 and body is None:
            body = {"token": _jwt({"cnf": {"jkt": ak.local_kid()}}), "expires_in": 300, "aud": "windy-mind"}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


def _mint(answers: list[tuple[int, dict | None]]):
    return ak.request_agent_token("windy-mind", transport=_eternitas(answers), min_ttl=10 ** 9)


RETIRED = (403, {"detail": {"code": "agent_retired", "message": "retired",
                            "retired_until": "2026-12-04T00:00:00Z"}})


# ── the latch ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_refused_mint_latches_the_stop_with_eternitas_own_date(tmp_path):
    _registered_key()
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([RETIRED])
    assert e.value.code == "agent_retired"
    stop = standing.stopped()
    assert stop["kind"] == "retired" and stop["retired_until"] == "2026-12-04T00:00:00Z"
    assert standing.answer(stop, name="Pip") == ("Pip is retired, so it isn't answering. You can restore it until "
                                                 "December 4, 2026. After that it stays closed, and its history is "
                                                 "kept.")


@pytest.mark.parametrize("status,body", [
    (429, {"detail": {"code": "rate_limited"}}),
    (401, {"detail": {"code": "invalid_dpop_proof"}}),
    (409, {"detail": {"code": "passport_not_born"}}),
    (500, {}),
])
def test_other_refusals_are_never_a_stop(status, body):
    _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([(status, body), (status, body)])
    assert standing.stopped() is None


def test_the_stop_survives_a_restart_and_only_a_good_mint_lifts_it():
    _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([RETIRED])
    standing._cache.update(mtime=None, value=None)   # a restart: nothing in memory, the file is the truth
    assert standing.stopped()["kind"] == "retired"
    with pytest.raises(ak.AgentTokenError):
        _mint([RETIRED])                              # still retired: still stopped
    assert standing.stopped() is not None
    _mint([(200, None)])                              # restored: the next good mint lifts it
    assert standing.stopped() is None


def test_a_legacy_refresh_refusal_latches_and_its_200_lifts(monkeypatch, tmp_path):
    from windyfly.eternitas import ept_refresh

    resp = httpx.Response(403, json={"detail": {"code": "passport_suspended", "message": "suspended"}})
    ak._standing_from_refusal(resp, "passport_suspended")
    assert standing.stopped()["kind"] == "suspended"
    assert "_standing_good_mint" in open(ept_refresh.__file__, encoding="utf-8").read()
    ak._standing_good_mint()
    assert standing.stopped() is None


# ── no model, no lifeboat, no tool; one line for a person, nothing for an agent ─────────────────────────────────────

def _respond(monkeypatch, *, agent_sender: bool = False, session: str = "sess-standing"):
    from windyfly.agent.loop import agent_respond
    from windyfly.channels import silence
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

    cfg = {"agent": {"default_model": "claude-sonnet-4-6"}, "memory": {}, "personality": {"preset": "buddy"}}
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "p.db"))
        wq = WriteQueue()
        wq.start()
        try:
            if agent_sender:
                silence.mark_agent_sender(session)
            with patch("windyfly.agent.loop.call_llm", side_effect=AssertionError("no model call")), \
                    patch("windyfly.agent.loop.get_offline_response", side_effect=AssertionError("no lifeboat")):
                return agent_respond(cfg, db, wq, "are you there?", session, ToolRegistry())
        finally:
            silence.clear_agent_sender(session)
            wq.stop()
            db.close()


def test_a_stopped_agent_makes_no_model_call_and_says_one_plain_line(monkeypatch):
    standing.latch("agent_retired", retired_until="2026-12-04T00:00:00Z")
    monkeypatch.setattr(standing, "_agent_name", lambda: "Pip")
    assert _respond(monkeypatch) == ("Pip is retired, so it isn't answering. You can restore it until December 4, "
                                     "2026. After that it stays closed, and its history is kept.")


def test_a_stopped_agent_posts_nothing_to_another_agent(monkeypatch):
    standing.latch("passport_suspended")
    assert _respond(monkeypatch, agent_sender=True) == ""


def test_call_llm_never_reaches_mind_a_provider_or_the_lifeboat_while_stopped(monkeypatch):
    standing.latch("passport_revoked")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("no model call while stopped"))
    monkeypatch.setattr(models, "_build_chain", lambda *a, **k: pytest.fail("no direct chain"))
    with pytest.raises(standing.AgentStopped):
        models.call_llm([{"role": "user", "content": "hi"}])


def _resp(status, headers=None, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {k.lower(): v for k, v in (headers or {}).items()}
    r.json.return_value = body or {}
    r.text = json.dumps(body or {})
    return r


@pytest.mark.parametrize("headers,body", [
    ({"x-mind-error": "agent_retired"}, {}),
    ({}, {"error": {"code": "agent_retired"}}),
])
def test_mind_refusing_a_retired_agent_is_a_stop_never_the_lifeboat(monkeypatch, headers, body):
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test")
    posts = []
    monkeypatch.setattr("httpx.post", lambda url, **kw: posts.append(url) or _resp(401, headers, body))
    monkeypatch.setattr(models, "_build_chain", lambda *a, **k: pytest.fail("no direct chain / lifeboat"))
    with pytest.raises(standing.AgentStopped):
        models.call_llm([{"role": "user", "content": "hi"}], config={"agent": {"default_model": "auto"}})
    assert standing.stopped()["kind"] == "retired" and len(posts) == 1


def test_no_tool_runs_while_stopped():
    import asyncio

    from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
    from windyfly.agent.capabilities.registry import CapabilityDenied, CapabilityRegistry

    ran = []
    reg = CapabilityRegistry()
    reg.register(Capability(id="t.send", tier=Tier.PURE_COMPUTE, description="send",
                            handler=lambda **k: ran.append(1)))
    standing.latch("agent_retired")
    with pytest.raises(CapabilityDenied):
        asyncio.run(reg.invoke("t.send", {}, Band.OWNER))
    assert ran == []
    standing.clear()
    asyncio.run(reg.invoke("t.send", {}, Band.OWNER))
    assert ran == [1]


# ── home 1.1 H13a: computer B's key is revoked after a move that didn't finish ──────────────────────────────────────

def test_b_key_revoked_mint_refused_and_the_latch_holds_across_a_restart(monkeypatch):
    _registered_key()
    revoked = (401, {"detail": {"code": "agent_key_revoked", "message": "revoked", "reason": "move_aborted"}})
    with pytest.raises(ak.AgentTokenError):
        _mint([revoked])
    standing._cache.update(mtime=None, value=None)   # a restart
    monkeypatch.setattr(standing, "_agent_name", lambda: "Pip")
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "move_aborted"
    assert _respond(monkeypatch) == ("The move to this computer didn't finish, so Pip is still running where it was, "
                                     "not on this computer. To try again, run windy home pair for a new code.")


def test_a_bare_key_revoke_gets_the_neutral_line_never_the_move_line(monkeypatch):
    standing.latch("agent_key_revoked")
    assert standing.answer(name="Pip") == "Pip can't run on this computer right now, so it isn't answering here."


@pytest.mark.parametrize("code,line", [
    ("agent_retired", "Pip is retired, so it isn't answering. You can restore it for 30 days after it was retired, "
                      "and its history is kept."),
    ("passport_suspended", "Pip is suspended, so it isn't answering right now."),
    ("passport_revoked", "Pip was closed, so it isn't answering."),
])
def test_each_stop_has_its_plain_line(code, line):
    standing.latch(code)                                     # no retired_until: the date is left out, never guessed
    assert standing.answer(name="Pip") == line


def test_a_bad_retired_until_is_left_out_not_guessed():
    standing.latch("agent_retired", retired_until="soon")
    assert "until" not in standing.answer(name="Pip")


# ── the 60 s probe: quiet within N seconds even when nobody writes ──────────────────────────────────────────────────

def test_n_is_the_probe_interval_plus_one_mint():
    assert (standing.PROBE_S, standing.MINT_TIMEOUT_S, standing.QUIET_WITHIN_S) == (60.0, 15.0, 75)


def test_the_probe_mints_fresh_and_latches_on_a_refusal():
    _registered_key()
    real, seen = ak.request_agent_token, []

    def fresh(aud, **kw):
        seen.append(kw.get("min_ttl"))
        return real(aud, transport=_eternitas([RETIRED]), min_ttl=kw["min_ttl"])

    with patch.object(ak, "request_agent_token", fresh):
        assert standing.probe_once() == "stopped"
    assert standing.stopped()["kind"] == "retired" and seen == [10 ** 9]     # never the cached token


def test_the_probe_runs_in_the_background_until_stopped():
    calls = []
    ev = threading.Event()

    def fake():
        calls.append(time.monotonic())
        if len(calls) >= 3:
            ev.set()

    with patch.object(standing, "_mint", fake):
        standing.start_probe(interval=0.01)
        standing.start_probe(interval=0.01)                  # idempotent: one thread
        assert ev.wait(5)
        standing.stop_probe()
    assert len(calls) >= 3


def test_the_probe_never_dies_on_an_error():
    seen = []
    ev = threading.Event()

    def boom():
        seen.append(1)
        if len(seen) >= 2:
            ev.set()
        raise RuntimeError("down")

    with patch.object(standing, "_mint", boom):
        standing.start_probe(interval=0.01)
        assert ev.wait(5)
        standing.stop_probe()


def test_a_passport_token_agent_sees_a_retire_on_the_public_read_and_a_restore_lifts_it(monkeypatch):
    from windyfly.eternitas import ept_refresh

    statuses = iter(["retired", "retired", "active"])
    monkeypatch.setattr(standing, "_verify_status", lambda p: next(statuses))
    refreshes = []
    monkeypatch.setattr(ept_refresh, "refresh_ept",
                        lambda force=False, **kw: refreshes.append(force) or ak._standing_good_mint() or
                        {"status": "refreshed"})
    assert standing.probe_once() == "stopped" and standing.stopped()["kind"] == "retired"
    assert standing.probe_once() == "stopped" and refreshes == []   # still retired: no refresh a minute
    assert standing.probe_once() == "ok" and refreshes == [True]    # active again: ONE forced refresh, lifted
    assert standing.stopped() is None


def test_unreachable_eternitas_is_no_news_never_a_stop(monkeypatch):
    monkeypatch.setattr(standing, "_verify_status", lambda p: None)
    assert standing.probe_once() == "ok" and standing.stopped() is None
