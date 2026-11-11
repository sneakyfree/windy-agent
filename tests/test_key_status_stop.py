"""A refused mint that says WHY, read the way Eternitas sends it (agent-key-ops.v1, eternitas #349).

With Eternitas's HOME_HANDOVER_DOORS on, a mint or a key-proof renew refused because this computer's key is no longer
active keeps its 401 and its code (``invalid_dpop_proof`` on the mint, ``invalid_agent_proof`` on the renew) and adds
``key_status`` and, for a revoked key, ``reason``. The spec: a client reads those two, never the code. Before this
file the agent recognised a revoked key only by codes Eternitas never sends, so it recorded no stop, the owner never
heard that a move had not finished, and ``windy home pair`` on that computer kept refusing.

The bodies below are Eternitas's own, copied from its tests (tests/test_handover_doors.py at 267708c1).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from windyfly.agent import standing
from windyfly.eternitas import agent_keys as ak

PASSPORT = "ET26-KSTS-0001"
NEUTRAL = "Pip can't run on this computer right now, so it isn't answering here."
MOVE_LINE = ("The move to this computer didn't finish, so Pip is still running where it was, not on this computer. "
             "To try again, run windy home pair for a new code.")
# LEGAL DECISION 58, BOARD 2026-11-11T07:07:42Z, word for word (NAME = the agent's name).
LEGAL_58_MOVED_AWAY = "NAME has moved to another computer, so it isn't answering here anymore."
LEGAL_58_NEUTRAL = "NAME can't run on this computer right now, so it isn't answering here."


def _mint_refusal(key_status: str | None = "revoked", reason: str | None = None) -> tuple[int, dict]:
    detail: dict = {"code": "invalid_dpop_proof", "message": f"agent key is {key_status or 'revoked'}"}
    if key_status is not None:
        detail["key_status"] = key_status
    if reason is not None:
        detail["reason"] = reason
    return 401, {"detail": detail}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.setattr(ak, "MINT_RETRY_S", 0.0)
    standing._cache.update(mtime=None, value=None)
    standing._revoked["next_at"] = 0.0
    standing._key_backoff.reset()
    standing._refresh_backoff.reset()
    ak._TOKEN_CACHE.clear()
    ak.clear_mint_wait()
    yield
    standing.stop_probe()
    ak._TOKEN_CACHE.clear()
    ak.clear_mint_wait()


def _registered_key() -> str:
    kid = ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-01T00:00:00Z", passport=PASSPORT)
    ak.save_credentials(data)
    return kid


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'ES256', 'typ': 'EPT+agent'})}.{enc(claims)}.SIG"


def _mint(answers: list[tuple[int, dict | None]], *, before_answer=None):
    def handle(req: httpx.Request) -> httpx.Response:
        if before_answer:
            before_answer()
        status, body = answers.pop(0)
        if status == 200 and body is None:
            body = {"token": _jwt({"cnf": {"jkt": ak.local_kid()}}), "expires_in": 300, "aud": "windy-mind"}
        return httpx.Response(status, json=body)

    return ak.request_agent_token("windy-mind", transport=httpx.MockTransport(handle), min_ttl=10 ** 9)


# ── the mint ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_revoked_key_with_move_aborted_is_the_stop_and_the_owner_hears_the_move_line():
    _registered_key()
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([_mint_refusal("revoked", "move_aborted")])
    assert e.value.code == "key_revoked" and e.value.status == 401
    standing._cache.update(mtime=None, value=None)   # a restart: the file is the truth
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "move_aborted"
    assert standing.answer(stop, name="Pip") == MOVE_LINE
    assert standing.answer(stop, name="Pip", owner=False) == "Pip isn't answering right now."


def test_moved_away_says_legal_s_sentence_to_the_owner_and_the_neutral_one_to_anyone_else():
    _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([_mint_refusal("revoked", "moved_away")])
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "moved_away"
    assert standing.answer(stop, name="Pip") == LEGAL_58_MOVED_AWAY.replace("NAME", "Pip")
    assert standing.answer(stop, name="Pip", owner=False) == "Pip isn't answering right now."


@pytest.mark.parametrize("reason", ["owner_revoked", "self_revoked", "ept_cutoff", "passport_revoked", "other"])
def test_every_other_reason_is_the_same_stop_with_the_neutral_line(reason):
    _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([_mint_refusal("revoked", reason)])
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == reason
    assert standing.answer(stop, name="Pip") == NEUTRAL == LEGAL_58_NEUTRAL.replace("NAME", "Pip")


def test_a_retired_key_is_the_same_stop_with_no_reason():
    _registered_key()
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([_mint_refusal("retired")])
    assert e.value.code == "key_revoked"
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and "reason" not in stop
    assert standing.answer(stop, name="Pip") == NEUTRAL


def test_with_eternitas_switch_off_nothing_changes():
    """Today's answer: the same 401 and code, no key_status. Still no stop, and the code is passed on as it came."""
    _registered_key()
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([_mint_refusal(None), _mint_refusal(None)])
    assert e.value.code == "invalid_dpop_proof"
    assert standing.stopped() is None


@pytest.mark.parametrize("status,detail", [
    (401, {"code": "invalid_dpop_proof", "key_status": "active"}),          # not a dead key
    (401, {"code": "invalid_dpop_proof", "key_status": 7}),
    (401, {"code": "invalid_dpop_proof", "key_status": None, "reason": "moved_away"}),
    (403, {"code": "forbidden", "key_status": "revoked", "reason": "moved_away"}),   # only a 401 says it
    (500, {"code": "oops", "key_status": "revoked"}),
])
def test_only_a_401_that_names_a_dead_key_counts(status, detail):
    _registered_key()
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([(status, {"detail": detail}), (status, {"detail": detail})])
    assert e.value.code != "key_revoked"
    assert standing.stopped() is None


def test_a_passport_stop_beside_key_status_keeps_its_own_code():
    _registered_key()
    body = {"detail": {"code": "passport_revoked", "message": "revoked", "key_status": "revoked",
                       "reason": "passport_revoked"}}
    with pytest.raises(ak.AgentTokenError) as e:
        _mint([(401, body)])
    assert e.value.code == "passport_revoked"
    assert standing.stopped()["kind"] == "revoked"


def test_a_key_this_computer_replaced_meanwhile_is_no_stop():
    """Its own rotation retired the old key while an ask with it was on its way: the next ask uses the new key."""
    _registered_key()

    def rotate_locally() -> None:
        ak.credentials_path().unlink()
        _registered_key()

    with pytest.raises(ak.AgentTokenError) as e:
        _mint([_mint_refusal("retired")], before_answer=rotate_locally)
    assert e.value.code == "key_revoked"
    assert standing.stopped() is None


def test_a_good_mint_with_the_key_lifts_it():
    _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([_mint_refusal("revoked", "move_aborted")])
    assert standing.stopped() is not None
    _mint([(200, None)])
    assert standing.stopped() is None


# ── the probe backs off, and windy home pair may set the key aside ────────────────────────────────────────

def test_the_probe_backs_off_as_for_any_key_revoked_stop(monkeypatch):
    _registered_key()
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(standing.time, "time", lambda: t["now"])
    asked: list[float] = []

    def refused(aud, **kw):
        asked.append(t["now"])
        resp = httpx.Response(*_mint_refusal("revoked", "moved_away")[:1], json=_mint_refusal("revoked", "moved_away")[1])
        code, msg = ak.refusal_code(resp)
        ak._standing_from_refusal(resp, code, kid=ak.active_kid())
        raise ak.AgentTokenError(code, msg, 401)

    monkeypatch.setattr(ak, "request_agent_token", refused)
    for _ in range(10):
        standing.probe_once()
        t["now"] += standing.PROBE_S
    assert standing.stopped()["kind"] == "key_revoked"
    assert len(asked) == 4                  # 0, +60, +180, +420 s within 10 minutes, not 10 asks


def test_home_pair_sees_the_revoked_key_after_such_a_stop():
    from windyfly import home_pair

    kid = _registered_key()
    with pytest.raises(ak.AgentTokenError):
        _mint([_mint_refusal("revoked", "moved_away")])

    def keys(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [{"kid": kid, "status": "revoked"}]})

    assert home_pair._revoked_key_here(httpx.MockTransport(keys)) is True


# ── the key-proof renew says it too ───────────────────────────────────────────────────────────────────────

def _renew_transport(proof_answer: tuple[int, dict], seen: list[str]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/keys/challenge"):
            return httpx.Response(200, json={"nonce": "nonce-1"})
        assert req.headers.get("Eternitas-Agent-Proof") and "authorization" not in req.headers
        return httpx.Response(proof_answer[0], json=proof_answer[1])

    return httpx.MockTransport(handle)


def test_a_renew_refused_for_a_revoked_key_records_the_stop_and_falls_back():
    from windyfly.eternitas import ept_refresh

    _registered_key()
    seen: list[str] = []
    answer = (401, {"detail": {"code": "invalid_agent_proof", "message": "key is revoked",
                               "key_status": "revoked", "reason": "moved_away"}})
    url = f"https://eternitas.test/api/v1/bots/{PASSPORT}/ept/refresh"
    assert ept_refresh._refresh_by_agent_key(PASSPORT, url, _renew_transport(answer, seen)) is None
    assert seen[-1].endswith("/ept/refresh")
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "moved_away"


def test_a_renew_refused_as_today_records_nothing():
    from windyfly.eternitas import ept_refresh

    _registered_key()
    answer = (401, {"detail": {"code": "invalid_agent_proof", "message": "key is revoked"}})
    url = f"https://eternitas.test/api/v1/bots/{PASSPORT}/ept/refresh"
    assert ept_refresh._refresh_by_agent_key(PASSPORT, url, _renew_transport(answer, [])) is None
    assert standing.stopped() is None


def test_a_token_path_renew_never_lifts_a_key_stop_but_lifts_any_other():
    standing.latch("key_revoked", reason="moved_away")
    ak._standing_good_mint(by_key=False)
    assert standing.stopped()["kind"] == "key_revoked"
    ak._standing_good_mint()
    assert standing.stopped() is None

    standing.latch("passport_suspended")
    ak._standing_good_mint(by_key=False)             # a restore seen on a token-path renew still lifts these
    assert standing.stopped() is None
