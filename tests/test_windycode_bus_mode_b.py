"""Fly -> Windy Code IDE agent bus, mode B (EPT+agent aud windy-code + DPoP in the auth message), DARK behind
WINDY_CODE_BUS_EPT_AGENT. Built to Code's contract of 11-04 (Eternitas ruled): htu "urn:windycode:agent-bus", htm
AUTH, refusals {"status":"error","error":..,"code":..}, one auth per connection (the bus closes after an auth error).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading

import pytest

from windyfly.agent import service_auth
from windyfly.eternitas import agent_keys as ak
from windyfly.tools import windycode as wc

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")

STORED = "stored-ept-value"


class FakeBus:
    """Answers each connection's FIRST line (the auth) from a script; closes after an auth error (Code's rule)."""

    def __init__(self, path: str) -> None:
        self.answers: list[dict] = []
        self.auths: list[dict] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                f = conn.makefile("rwb")
                line = f.readline()
                if not line:
                    continue
                self.auths.append(json.loads(line))
                ans = self.answers.pop(0) if self.answers else {"id": "auth", "status": "ok"}
                f.write((json.dumps(ans) + "\n").encode())
                f.flush()
                if ans.get("status") != "ok":
                    continue  # closes: a second auth on this socket is never read
                for cmd in f:
                    req = json.loads(cmd)
                    f.write((json.dumps({"id": req.get("id"), "status": "ok", "result": {"exitCode": 0}}) + "\n")
                            .encode())
                    f.flush()

    def kinds(self) -> list[str]:
        return ["mode-b" if "dpop" in a else ("stored" if a.get("windy_jwt") == STORED else "?") for a in self.auths]

    def close(self) -> None:
        self.srv.close()


def _err(code: str) -> dict:
    return {"id": "auth", "status": "error", "error": "refused (free text that may change)", "code": code}


@pytest.fixture
def bus(monkeypatch):
    d = tempfile.mkdtemp(prefix="wcb-")
    path = os.path.join(d, "bus.sock")
    monkeypatch.setattr(wc, "_socket_path", lambda: path)
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-BUSB")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", STORED)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.setenv(wc.BUS_FLAG, "1")
    state = {"mints": 0, "forgot": 0, "proofs": [], "mint_fails": False}

    def mint(aud, *a, **k):
        if state["mint_fails"]:
            raise ak.AgentTokenError("agent_retired", "", 403)
        assert aud == "windy-code"
        if state["forgot"] >= state["mints"]:
            state["mints"] += 1
        return {"token": f"agent-token-{state['mints']}"}

    def proof(htm, htu_raw, **k):
        state["proofs"].append((htm, htu_raw))
        return f"proof-{len(state['proofs'])}"

    monkeypatch.setattr(ak, "request_agent_token", mint)
    monkeypatch.setattr(ak, "bus_dpop", proof)
    monkeypatch.setattr(service_auth, "forget_token", lambda: state.__setitem__("forgot", state["forgot"] + 1))
    fake = FakeBus(path)
    fake.state = state
    yield fake
    fake.close()
    if os.path.exists(path):
        os.unlink(path)
    os.rmdir(d)


def _status():
    return wc.windycode_status()


def test_flag_off_is_todays_auth_byte_for_byte(bus, monkeypatch):
    monkeypatch.delenv(wc.BUS_FLAG)
    assert _status()["status"] == "connected"
    assert bus.auths == [{"type": "auth", "eternitas_passport": "ET26-TEST-BUSB", "windy_jwt": STORED}]
    assert bus.state["mints"] == 0


def test_flag_on_sends_the_agent_token_and_a_proof_for_the_bus_urn(bus):
    assert _status()["status"] == "connected"
    assert bus.auths == [{"type": "auth", "eternitas_passport": "ET26-TEST-BUSB", "windy_jwt": "agent-token-1",
                          "dpop": "proof-1"}]
    assert bus.state["proofs"] == [("AUTH", "urn:windycode:agent-bus")]  # verbatim, never a socket path


def test_every_connection_gets_a_fresh_proof(bus):
    for _ in range(3):
        _status()
    assert len({a["dpop"] for a in bus.auths}) == 3


def test_ide_mode_b_off_falls_back_on_a_new_connection(bus):
    bus.answers = [_err("wrong_token_type")]
    assert _status()["status"] == "connected"
    assert bus.kinds() == ["mode-b", "stored"] and bus.state["forgot"] == 0


@pytest.mark.parametrize("code", ["dpop_required", "invalid_dpop", "dpop_replay"])
def test_a_proof_code_gets_one_new_proof_same_token_never_a_fallback(bus, code):
    bus.answers = [_err(code), _err(code)]
    out = _status()
    assert out["status"] == "unavailable" and bus.kinds() == ["mode-b", "mode-b"]
    assert bus.auths[0]["windy_jwt"] == bus.auths[1]["windy_jwt"] and bus.auths[0]["dpop"] != bus.auths[1]["dpop"]


@pytest.mark.parametrize("code", ["wrong_audience", "expired", "invalid_token"])
def test_a_token_code_gets_one_new_mint_never_a_fallback(bus, code):
    bus.answers = [_err(code), _err(code)]
    out = _status()
    assert out["status"] == "unavailable" and bus.kinds() == ["mode-b", "mode-b"]
    assert bus.state["forgot"] == 1 and bus.auths[0]["windy_jwt"] != bus.auths[1]["windy_jwt"]


def test_a_retry_that_lands_is_connected(bus):
    bus.answers = [_err("dpop_replay")]
    assert _status()["status"] == "connected" and bus.kinds() == ["mode-b", "mode-b"]


def test_a_retry_answered_wrong_token_type_then_falls_back_once(bus):
    bus.answers = [_err("expired"), _err("wrong_token_type")]
    assert _status()["status"] == "connected" and bus.kinds() == ["mode-b", "mode-b", "stored"]


@pytest.mark.parametrize("code", ["passport_revoked", "passport_suspended", "ept_cutoff", "standing_unavailable",
                                  "too_many_connections", "identity_locked", "something_new", ""])
def test_every_other_code_stops_with_no_fallback(bus, code):
    bus.answers = [_err(code)]
    out = _status()
    assert out["status"] == "unavailable" and bus.kinds() == ["mode-b"]


def test_the_stop_words_name_the_reason_not_the_free_text(bus):
    bus.answers = [_err("standing_unavailable")]
    assert _status()["message"] == wc._BUS_STOP_SAY["standing_unavailable"]


def test_no_mint_uses_the_stored_ept(bus):
    bus.state["mint_fails"] = True
    assert _status()["status"] == "connected" and bus.kinds() == ["stored"]


def test_no_stored_ept_means_no_fallback_connection(bus, monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    bus.answers = [_err("wrong_token_type")]
    assert _status()["status"] == "unavailable" and bus.kinds() == ["mode-b"]


def test_logs_carry_codes_never_tokens(bus, caplog):
    bus.answers = [_err("wrong_token_type")]
    with caplog.at_level(logging.INFO, logger=wc.__name__):
        _status()
    assert "[code-bus-auth]" in caplog.text and "wrong_token_type" in caplog.text
    assert STORED not in caplog.text and "agent-token" not in caplog.text and "proof-" not in caplog.text


def test_bus_dpop_signs_the_urn_verbatim(monkeypatch):
    """The real helper (no bus fixture here): htu is never URL-normalised, the jti is in Code's 8-128 range."""
    import base64

    key = ak.generate_private_key()
    monkeypatch.setattr(ak, "_active_key", lambda cred_path: (key, "kid"))
    seg = ak.bus_dpop("AUTH", "urn:windycode:agent-bus").split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    assert claims["htu"] == "urn:windycode:agent-bus" and claims["htm"] == "AUTH"
    assert 8 <= len(claims["jti"]) <= 128
