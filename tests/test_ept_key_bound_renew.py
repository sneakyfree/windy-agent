"""A 24-hour passport token bound to this computer's key (Eternitas ept-key-binding.v1, eternitas #350).

With Eternitas's EPT_KEY_BOUND on, a renew signed by the agent's key returns a token that names that key (``akid``),
lives 24 hours and is stored nowhere at Eternitas; the reply carries ``expires_in``. Three rules, each pinned here:

1. it is renewed at half its life, by a check on every maintenance tick (once a calendar day is not once per 24 h);
2. this computer keeps no copy of it: no ``.bak-ept-`` copy of an env file that holds one, and the older copies go
   when the first one arrives;
3. a renew refused with ``ept_key_inactive`` stops the agent only when the token names the key this computer uses now.

The refusal bodies are Eternitas's own (services/agent_keys.py ``bound_key_or_refuse`` at 4bac6479).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from windyfly.agent import maintenance, standing
from windyfly.eternitas import agent_keys as ak
from windyfly.eternitas import ept_refresh as er

PASSPORT = "ET26-TEST-KBND"
HOUR = 3600
DAY = 24 * HOUR
T0 = 1_800_000_000.0


def make_ept(*, iat: float | None, exp: float, akid: str | None = None, cvr: bool = True) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims: dict = {"sub": PASSPORT, "exp": int(exp), "iss": "eternitas.ai"}
    if iat is not None:
        claims["iat"] = int(iat)
    if akid is not None:
        claims["akid"] = akid
    if cvr:
        claims["cvr"] = 3
    return f"{b64({'alg': 'ES256', 'typ': 'EPT'})}.{b64(claims)}.c2ln"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    monkeypatch.delenv(er.ENV_KEY, raising=False)
    monkeypatch.setenv(er.PASSPORT_KEY, PASSPORT)
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    import windyfly.hub_login as hl

    monkeypatch.setattr(hl, "get_access_token", lambda *a, **k: None)
    standing._cache.update(mtime=None, value=None)
    er._renew_at.clear()
    yield
    er._renew_at.clear()
    standing._cache.update(mtime=None, value=None)


def env_file(tmp_path, token: str, monkeypatch, mode: int = 0o600):
    p = tmp_path / "agent.env"
    p.write_text(f"FIRST=keepme\n{er.ENV_KEY}={token}\nOTHER=1\n", encoding="utf-8")
    os.chmod(p, mode)
    monkeypatch.setenv("WINDY_ENV_FILE", str(p))
    monkeypatch.setenv(er.ENV_KEY, token)
    return p


def _registered_key() -> str:
    kid = ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-01T00:00:00Z", passport=PASSPORT)
    ak.save_credentials(data)
    return kid


def bearer_transport(calls: list, answer: tuple[int, dict]) -> httpx.MockTransport:
    """An Eternitas where the key proof is refused the way it is today (no key_status), so the Bearer path runs."""
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/keys/challenge"):
            return httpx.Response(200, json={"nonce": "nonce-1"})
        if req.headers.get("Eternitas-Agent-Proof"):
            return httpx.Response(401, json={"detail": {"code": "invalid_agent_proof", "message": "key is revoked"}})
        calls.append(req)
        return httpx.Response(answer[0], json=answer[1])

    return httpx.MockTransport(handle)


def renew_reply(token: str, expires_in: int | None = DAY) -> tuple[int, dict]:
    body = {"passport": PASSPORT, "ept_token": token, "expires_at": "2026-11-12T00:00:00+00:00",
            "reissued": True, "reissue_reason": "key_bound"}
    if expires_in is not None:
        body["expires_in"] = expires_in
    return 200, body


# ── 1. when to renew ────────────────────────────────────────────────────────────────────────────────────────

def test_a_key_bound_token_is_renewed_at_half_its_life_not_by_the_30_day_rule():
    token = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    assert er.is_key_bound(token)
    assert er.needs_refresh(token, now=T0 + 60) == (False, "current")
    assert er.needs_refresh(token, now=T0 + 12 * HOUR - 60) == (False, "current")
    assert er.needs_refresh(token, now=T0 + 12 * HOUR + 60) == (True, "half_life")
    assert er.needs_refresh(token, now=T0 + DAY + 1) == (True, "expired")


def test_a_key_bound_token_with_no_iat_is_measured_as_24_hours():
    token = make_ept(iat=None, exp=T0 + DAY, akid="kid-a")
    assert er.needs_refresh(token, now=T0 + 11 * HOUR) == (False, "current")
    assert er.needs_refresh(token, now=T0 + 13 * HOUR) == (True, "half_life")


def test_a_year_long_token_keeps_its_30_day_rule():
    assert not er.is_key_bound(make_ept(iat=T0, exp=T0 + 365 * DAY))
    assert er.renew_time(make_ept(iat=T0, exp=T0 + 365 * DAY)) is None
    assert er.needs_refresh(make_ept(iat=T0, exp=T0 + 365 * DAY), now=T0 + 200 * DAY) == (False, "current")
    assert er.needs_refresh(make_ept(iat=T0, exp=T0 + 365 * DAY), now=T0 + 340 * DAY) == (True, "expiring")


@pytest.mark.parametrize("akid", ["", 7, None, ["kid-a"]])
def test_only_a_named_key_makes_a_token_key_bound(akid):
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = f"{b64({'alg': 'ES256'})}.{b64({'sub': PASSPORT, 'exp': int(T0 + DAY), 'akid': akid, 'cvr': 3})}.c2ln"
    assert not er.is_key_bound(token)


def test_expires_in_from_the_reply_sets_the_renew_time(tmp_path, monkeypatch):
    """Eternitas says 1 hour although the token's own claims say 24: the reply wins, by this computer's clock."""
    old = make_ept(iat=T0 - DAY, exp=T0 + 60, akid="kid-a")
    new = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    env_file(tmp_path, old, monkeypatch)
    calls: list = []
    res = er.refresh_ept(transport=bearer_transport(calls, renew_reply(new, expires_in=HOUR)), now=T0)
    assert res["status"] == "refreshed" and res["key_bound"] is True and len(calls) == 1
    assert er.needs_refresh(new, now=T0 + 29 * 60) == (False, "current")
    assert er.needs_refresh(new, now=T0 + 31 * 60) == (True, "half_life")


@pytest.mark.parametrize("bad", [0, -5, "86400", True, None])
def test_a_useless_expires_in_is_ignored_and_the_token_s_own_life_is_used(tmp_path, monkeypatch, bad):
    old = make_ept(iat=T0 - DAY, exp=T0 + 60, akid="kid-a")
    new = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    env_file(tmp_path, old, monkeypatch)
    status, body = renew_reply(new, expires_in=None)
    body["expires_in"] = bad
    er.refresh_ept(transport=bearer_transport([], (status, body)), now=T0)
    assert er._renew_at == {}
    assert er.needs_refresh(new, now=T0 + 12 * HOUR + 60) == (True, "half_life")


# ── the tick ────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_tick_asks_nothing_for_a_year_long_token_even_inside_its_30_days(tmp_path, monkeypatch):
    env_file(tmp_path, make_ept(iat=T0 - 340 * DAY, exp=T0 + 25 * DAY), monkeypatch)
    calls: list = []
    assert er.renew_short_lived(transport=bearer_transport(calls, (500, {})), now=T0) is None
    assert calls == []


def test_the_tick_asks_nothing_before_half_life_and_once_after(tmp_path, monkeypatch):
    old = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    new = make_ept(iat=T0 + 12 * HOUR, exp=T0 + 36 * HOUR, akid="kid-a")
    p = env_file(tmp_path, old, monkeypatch)
    calls: list = []
    t = bearer_transport(calls, renew_reply(new))
    for minutes in range(0, 12 * 60, 15):                       # 48 ticks of the first half: no ask at all
        assert er.renew_short_lived(transport=t, now=T0 + minutes * 60) is None
    assert calls == []
    res = er.renew_short_lived(transport=t, now=T0 + 12 * HOUR + 15 * 60)
    assert res and res["status"] == "refreshed" and len(calls) == 1
    assert calls[0].headers["authorization"] == f"Bearer {old}"
    assert f"{er.ENV_KEY}={new}" in p.read_text(encoding="utf-8")
    assert er.renew_short_lived(transport=t, now=T0 + 12 * HOUR + 30 * 60) is None      # the new one is fresh
    assert len(calls) == 1


def test_the_tick_asks_nothing_while_a_stop_is_in_force(tmp_path, monkeypatch):
    env_file(tmp_path, make_ept(iat=T0, exp=T0 + DAY, akid="kid-a"), monkeypatch)
    standing.latch("passport_suspended")
    calls: list = []
    assert er.renew_short_lived(transport=bearer_transport(calls, (500, {})), now=T0 + 13 * HOUR) is None
    assert calls == []


def test_a_failed_ask_is_tried_again_at_the_next_tick(tmp_path, monkeypatch):
    env_file(tmp_path, make_ept(iat=T0, exp=T0 + DAY, akid="kid-a"), monkeypatch)
    calls: list = []
    t = bearer_transport(calls, (503, {"detail": "busy"}))
    for k in range(3):
        res = er.renew_short_lived(transport=t, now=T0 + 13 * HOUR + k * 900)
        assert res and res["status"] == "failed"
    assert len(calls) == 3


def test_the_maintenance_list_has_the_tick_job_beside_the_daily_one():
    jobs = {j.name: j for j in maintenance.default_jobs({})}
    assert "eternitas.ept_refresh.daily" in jobs
    job = jobs["eternitas.ept_renew.short_lived"]
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    assert job.due(None, now)
    assert job.due(now - timedelta(seconds=maintenance.TICK_SECONDS - 1), now)       # a tick a second early
    assert not job.due(now - timedelta(seconds=60), now)


# ── 2. no copy of a key-bound token ─────────────────────────────────────────────────────────────────────────

def _copies(p) -> list:
    return sorted(p.parent.glob(f"{p.name}.bak-ept-*"))


def test_the_first_key_bound_token_leaves_no_copy_and_wipes_the_older_ones(tmp_path, monkeypatch):
    year = make_ept(iat=T0 - 340 * DAY, exp=T0 + 25 * DAY)
    new = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    p = env_file(tmp_path, year, monkeypatch, mode=0o640)
    older = p.with_name(f"{p.name}.bak-ept-20261001T000000Z")
    older.write_text(f"{er.ENV_KEY}=an-older-year-long-token\n", encoding="utf-8")
    other = p.with_name(f"{p.name}.bak-deregister-20261001T000000Z")
    other.write_text("X=1\n", encoding="utf-8")

    res = er.refresh_ept(transport=bearer_transport([], renew_reply(new)), now=T0)
    assert res["status"] == "refreshed" and res["persisted"] is True
    assert _copies(p) == []                                     # none made, and the older one is gone
    assert other.exists()                                       # not this function's file
    text = p.read_text(encoding="utf-8")
    assert text == f"FIRST=keepme\n{er.ENV_KEY}={new}\nOTHER=1\n"
    assert (p.stat().st_mode & 0o777) == 0o640
    assert year not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir() if f.is_file())


def test_renewing_a_key_bound_token_never_makes_a_copy(tmp_path, monkeypatch):
    old = make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid="kid-a")
    new = make_ept(iat=T0, exp=T0 + DAY, akid="kid-a")
    p = env_file(tmp_path, old, monkeypatch)
    assert er.refresh_ept(transport=bearer_transport([], renew_reply(new)), now=T0)["status"] == "refreshed"
    assert _copies(p) == []
    assert old not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir() if f.is_file())


def test_a_year_long_token_after_a_key_bound_one_makes_no_copy_of_the_key_bound_one(tmp_path, monkeypatch):
    """Eternitas's switch went back off, or the owner's sign-in renewed: the file that is replaced holds a key-bound
    token, so it is not copied."""
    old = make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid="kid-a")
    year = make_ept(iat=T0, exp=T0 + 365 * DAY)
    p = env_file(tmp_path, old, monkeypatch)
    assert er.refresh_ept(transport=bearer_transport([], renew_reply(year, expires_in=None)), now=T0)["status"] \
        == "refreshed"
    assert _copies(p) == []


def test_a_year_long_token_replacing_a_year_long_one_keeps_its_one_copy_as_before(tmp_path, monkeypatch):
    old = make_ept(iat=T0 - 340 * DAY, exp=T0 + 25 * DAY)
    year = make_ept(iat=T0, exp=T0 + 365 * DAY)
    p = env_file(tmp_path, old, monkeypatch)
    assert er.refresh_ept(transport=bearer_transport([], renew_reply(year, expires_in=None)), now=T0)["status"] \
        == "refreshed"
    copies = _copies(p)
    assert len(copies) == 1 and old in copies[0].read_text(encoding="utf-8")
    assert (copies[0].stat().st_mode & 0o777) == 0o600


# ── 3. ept_key_inactive ─────────────────────────────────────────────────────────────────────────────────────

def _inactive(key_status: str | None) -> tuple[int, dict]:
    if key_status is None:      # "not a key of this passport": Eternitas names no status
        return 401, {"detail": {"code": "ept_key_inactive",
                                "message": "This token's agent key is not a key of this passport"}}
    return 401, {"detail": {"code": "ept_key_inactive", "message": f"This token's agent key is {key_status}",
                            "key_status": key_status}}


@pytest.mark.parametrize("key_status", ["revoked", "retired"])
def test_a_refused_renew_for_this_computer_s_current_key_is_the_key_stop(tmp_path, monkeypatch, key_status):
    kid = _registered_key()
    env_file(tmp_path, make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid=kid), monkeypatch)
    calls: list = []
    res = er.refresh_ept(transport=bearer_transport(calls, _inactive(key_status)), now=T0)
    assert res == {"status": "failed", "http": 401, "code": "key_revoked"}        # and no sign-in hint
    assert len(calls) == 1
    standing._cache.update(mtime=None, value=None)
    assert standing.stopped()["kind"] == "key_revoked"


def test_a_token_that_names_a_key_this_computer_has_replaced_is_no_stop(tmp_path, monkeypatch):
    """Its own rotation retired the old key; the token still in the env file names it. The next ask signs with the
    new key, so nothing stops."""
    _registered_key()
    env_file(tmp_path, make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid="the-key-before-the-rotation"),
             monkeypatch)
    res = er.refresh_ept(transport=bearer_transport([], _inactive("retired")), now=T0)
    assert res == {"status": "failed", "http": 401, "code": "ept_key_inactive"}
    assert standing.stopped() is None


def test_a_refusal_that_names_no_key_status_is_no_stop(tmp_path, monkeypatch):
    kid = _registered_key()
    env_file(tmp_path, make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid=kid), monkeypatch)
    res = er.refresh_ept(transport=bearer_transport([], _inactive(None)), now=T0)
    assert res["code"] == "ept_key_inactive" and "hint" not in res
    assert standing.stopped() is None


def test_with_no_key_on_this_computer_it_is_no_stop(tmp_path, monkeypatch):
    env_file(tmp_path, make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid="some-key"), monkeypatch)
    res = er.refresh_ept(transport=bearer_transport([], _inactive("revoked")), now=T0)
    assert res["code"] == "ept_key_inactive"
    assert standing.stopped() is None


def test_it_never_overwrites_the_reason_the_key_s_own_refusal_gave(tmp_path, monkeypatch):
    kid = _registered_key()
    env_file(tmp_path, make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid=kid), monkeypatch)
    standing.latch("key_revoked", reason="moved_away")
    er.refresh_ept(force=True, transport=bearer_transport([], _inactive("revoked")), now=T0)
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "moved_away"


def test_the_same_code_on_a_year_long_token_s_renew_changes_nothing(tmp_path, monkeypatch):
    """Eternitas sends ept_key_inactive only for a token that names a key; if it ever came for one that names
    none, it is an ordinary refusal."""
    _registered_key()
    env_file(tmp_path, make_ept(iat=T0 - 340 * DAY, exp=T0 + 25 * DAY), monkeypatch)
    res = er.refresh_ept(transport=bearer_transport([], _inactive("revoked")), now=T0)
    assert res["status"] == "failed" and res["code"] == "ept_key_inactive"
    assert standing.stopped() is None


def test_a_token_is_never_in_a_log_line(tmp_path, monkeypatch, caplog):
    kid = _registered_key()
    old = make_ept(iat=T0 - 13 * HOUR, exp=T0 + 11 * HOUR, akid=kid)
    new = make_ept(iat=T0, exp=T0 + DAY, akid=kid)
    env_file(tmp_path, old, monkeypatch)
    with caplog.at_level("DEBUG"):
        er.refresh_ept(transport=bearer_transport([], renew_reply(new)), now=T0)
        er.refresh_ept(force=True, transport=bearer_transport([], _inactive("revoked")), now=T0)
    assert old not in caplog.text and new not in caplog.text and kid not in caplog.text


# ── 4. no owner sign-in after a refused key proof (Eternitas, BOARD 2026-11-11T08:18:15Z) ───────────────────

def key_then_bearer(calls: list, key_answer: tuple[int, dict], bearer_answer: tuple[int, dict]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/keys/challenge"):
            return httpx.Response(200, json={"nonce": "nonce-1"})
        calls.append(("key" if req.headers.get("Eternitas-Agent-Proof") else "bearer", req.headers.get("authorization")))
        return httpx.Response(*(key_answer if req.headers.get("Eternitas-Agent-Proof") else bearer_answer)[:1],
                              json=(key_answer if req.headers.get("Eternitas-Agent-Proof") else bearer_answer)[1])

    return httpx.MockTransport(handle)


def _signed_in(monkeypatch):
    import windyfly.hub_login as hl

    monkeypatch.setattr(hl, "get_access_token", lambda *a, **k: "owner-cached-signin")


REVOKED = (401, {"detail": {"code": "invalid_agent_proof", "message": "key is revoked", "key_status": "revoked",
                            "reason": "moved_away"}})


@pytest.mark.parametrize("key_answer", [
    REVOKED,
    (401, {"detail": {"code": "invalid_agent_proof", "message": "key is revoked"}}),     # today's body, no key_status
    (403, {"detail": {"code": "forbidden"}}),
])
def test_a_refused_key_proof_never_falls_back_to_the_owner_s_sign_in(tmp_path, monkeypatch, key_answer):
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 400 * DAY, exp=T0 - DAY), monkeypatch)        # the token has lapsed
    calls: list = []
    res = er.refresh_ept(transport=key_then_bearer(calls, key_answer, renew_reply(make_ept(iat=T0, exp=T0 + 365 * DAY),
                                                                                  expires_in=None)), now=T0)
    assert res["status"] == "failed" and res["via"] == "agent_key" and res["http"] == key_answer[0]
    assert [c[0] for c in calls] == ["key"]                       # no Bearer call at all
    assert all(c[1] != "Bearer owner-cached-signin" for c in calls)


def test_with_the_moved_away_answer_the_agent_is_stopped_with_its_sentence(tmp_path, monkeypatch):
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 400 * DAY, exp=T0 - DAY), monkeypatch)
    er.refresh_ept(transport=key_then_bearer([], REVOKED, (500, {})), now=T0)
    standing._cache.update(mtime=None, value=None)
    stop = standing.stopped()
    assert stop["kind"] == "key_revoked" and stop["reason"] == "moved_away"
    assert standing.answer(stop, name="Pip") == "Pip has moved to another computer, so it isn't answering here anymore."


def test_after_a_cutoff_a_refused_key_never_reaches_the_owner_s_sign_in_either(tmp_path, monkeypatch):
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 340 * DAY, exp=T0 + 25 * DAY), monkeypatch)    # still valid, but cut off
    calls: list = []
    cutoff = (401, {"detail": {"code": "ept_before_cutoff", "message": "issued before the cutoff"}})
    res = er.refresh_ept(transport=key_then_bearer(calls, REVOKED, cutoff), now=T0)
    assert res["status"] == "failed" and res["via"] == "agent_key"
    assert calls == [("key", None), ("bearer", f"Bearer {os.environ[er.ENV_KEY]}")]   # own token once, never the sign-in


@pytest.mark.parametrize("key_answer", [(429, {"detail": "slow down"}), (503, {"detail": "busy"}),
                                        (500, {"detail": "oops"})])
def test_a_busy_eternitas_never_sends_a_key_holder_to_the_owner_s_sign_in(tmp_path, monkeypatch, key_answer):
    """Eternitas RULING 2026-11-11T13:08:48Z: a key holder never renews with the owner's cached sign-in, whatever the
    key call returned; a transient failure is failed, and the next tick asks again by key."""
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 400 * DAY, exp=T0 - DAY), monkeypatch)
    calls: list = []
    new = make_ept(iat=T0, exp=T0 + 365 * DAY)
    res = er.refresh_ept(transport=key_then_bearer(calls, key_answer, renew_reply(new, expires_in=None)), now=T0)
    assert res["status"] == "failed" and res["via"] == "agent_key" and res["http"] == key_answer[0]
    assert [c[0] for c in calls] == ["key"]                     # the sign-in is never sent


def test_a_key_call_that_cannot_be_made_never_reaches_the_owner_s_sign_in(tmp_path, monkeypatch):
    """A timeout or a local signing error alike: failed, no bearer at all."""
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 400 * DAY, exp=T0 - DAY), monkeypatch)
    monkeypatch.setattr(ak, "agent_proof", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("signing failed")))
    calls: list = []
    res = er.refresh_ept(transport=key_then_bearer(calls, (200, {}), (200, {})), now=T0)
    assert res == {"status": "failed", "code": "key_call_failed", "via": "agent_key"}
    assert calls == []


def test_a_key_holder_whose_own_token_is_still_valid_may_use_it(tmp_path, monkeypatch):
    """Only the OWNER's sign-in is closed to a key holder; the agent's own unexpired token still renews."""
    _registered_key()
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 300 * DAY, exp=T0 + 60 * DAY), monkeypatch)
    calls: list = []
    new = make_ept(iat=T0, exp=T0 + 365 * DAY)
    res = er.refresh_ept(transport=key_then_bearer(calls, (503, {"detail": "busy"}), renew_reply(new, expires_in=None)),
                         now=T0, force=True)
    assert res["status"] == "refreshed" and res["via"] == "own_ept"
    assert "Bearer owner-cached-signin" not in [c[1] for c in calls]


def test_an_agent_with_no_key_keeps_the_owner_s_sign_in(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    env_file(tmp_path, make_ept(iat=T0 - 400 * DAY, exp=T0 - DAY), monkeypatch)
    calls: list = []
    new = make_ept(iat=T0, exp=T0 + 365 * DAY)
    res = er.refresh_ept(transport=key_then_bearer(calls, REVOKED, renew_reply(new, expires_in=None)), now=T0)
    assert res["status"] == "refreshed" and res["via"] == "owner_hub_login"
    assert [c[0] for c in calls] == ["bearer"]
