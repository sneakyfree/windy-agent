"""A stopped agent can always recover (Hub on #709).

- The 60 s probe starts whenever the agent runs (every runtime-claim outcome but CONFLICT), not only on GRANTED: while
  stopped it is the only thing that lifts the stop.
- A passport-token agent finds its passport from the stored token's sub when ETERNITAS_PASSPORT is unset.
- A refused forced refresh, and a key_revoked mint, back off (doubling, capped) instead of repeating every minute;
  a new local key is tried at once; a retired/suspended stop keeps the plain 60 s check.
- `windy home pair` after a move that didn't finish: a key Eternitas shows as revoked is set aside (renamed, never
  deleted) so a new one can pair; not sure = keep refusing.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

from windyfly.agent import standing
from windyfly.eternitas import agent_keys as ak

PASSPORT = "ET26-STND-0002"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    standing._cache.update(mtime=None, value=None)
    standing._key_backoff.reset()
    standing._refresh_backoff.reset()
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


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(standing.time, "time", c)
    return c


# ── the probe starts on every outcome but CONFLICT ──────────────────────────────────────────────

def test_main_starts_the_probe_outside_the_granted_branch():
    src = Path(__import__("windyfly.main", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "start_probe"]
    assert len(calls) == 1
    # the call is a statement in the SAME block as the claim's if/elif chain, AFTER it (not inside any branch)
    for node in ast.walk(tree):
        for field in ("body", "orelse"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            chain = [i for i, st in enumerate(block) if isinstance(st, ast.If) and "CONFLICT" in ast.unparse(st.test)]
            if not chain:
                continue
            here = [i for i, st in enumerate(block)
                    if isinstance(st, ast.Expr) and any(n is calls[0] for n in ast.walk(st))]
            assert here and here[0] > chain[0], "start_probe must run after the claim branches, at their level"
            return
    raise AssertionError("claim if/elif chain not found in main.py")


# ── the passport from the token's sub ───────────────────────────────────────────────────────────

def _token(sub: str) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'ES256'})}.{enc({'sub': sub, 'exp': 9999999999})}.SIG"


def test_a_passport_token_agent_without_the_env_passport_is_still_checked(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _token("ET26-FROM-SUB0"))
    seen = []
    monkeypatch.setattr(standing, "_verify_status", lambda p: seen.append(p) or "retired")
    assert standing.probe_once() == "stopped" and seen == ["ET26-FROM-SUB0"]
    assert standing.stopped()["kind"] == "retired"


# ── the refused forced refresh backs off ────────────────────────────────────────────────────────

def test_a_refused_forced_refresh_backs_off_then_a_good_one_lifts(monkeypatch, clock):
    from windyfly.eternitas import ept_refresh

    standing.latch("agent_retired")
    monkeypatch.setattr(standing, "_verify_status", lambda p: "active")
    tries = []
    answer = {"status": "failed", "code": "invalid_token"}
    monkeypatch.setattr(ept_refresh, "refresh_ept", lambda force=False, **k: tries.append(clock.t) or dict(answer))
    for _ in range(8):                      # 8 probe ticks, one a minute
        standing.probe_once()
        clock.t += standing.PROBE_S
    assert len(tries) == 4                  # at 0, +60, +180, +420 s: never once a minute
    clock.t += 3 * 3600
    answer.update(status="refreshed")
    monkeypatch.setattr(ept_refresh, "refresh_ept",
                        lambda force=False, **k: tries.append(clock.t) or ak._standing_good_mint() or dict(answer))
    assert standing.probe_once() == "ok" and standing.stopped() is None


def test_the_backoff_is_capped():
    b = standing._Backoff()
    for _ in range(20):
        b.failed(now=0.0)
    assert b.next_at == standing.BACKOFF_MAX_S


# ── key_revoked backs off; a new key is tried at once; retired keeps 60 s ───────────────────────

def _refusing(code: str, calls: list):
    def mint(aud, **kw):
        calls.append(ak.active_entry(ak.load_credentials())["kid"])
        err = ak.AgentTokenError(code, "refused", 401)
        ak._standing_from_refusal(httpx.Response(401, json={"detail": {"code": code}}), code)
        raise err
    return mint


def test_a_key_revoked_mint_backs_off(monkeypatch, clock):
    _registered_key()
    calls: list = []
    monkeypatch.setattr(ak, "request_agent_token", _refusing("agent_key_revoked", calls))
    for _ in range(10):
        standing.probe_once()
        clock.t += standing.PROBE_S
    assert standing.stopped()["kind"] == "key_revoked"
    assert len(calls) == 4                  # 0, +60, +180, +420 s within 10 minutes


def test_a_new_local_key_is_tried_at_once(monkeypatch, clock):
    _registered_key()
    calls: list = []
    monkeypatch.setattr(ak, "request_agent_token", _refusing("agent_key_revoked", calls))
    standing.probe_once()
    clock.t += standing.PROBE_S
    standing.probe_once()                   # second refusal: the next try is 120 s away
    clock.t += 1
    ak.credentials_path().unlink()          # `windy home pair` gave this computer a new key
    _registered_key()
    monkeypatch.setattr(ak, "request_agent_token", lambda aud, **kw: ak._standing_good_mint() or {"token": "t"})
    assert standing.probe_once() == "ok" and standing.stopped() is None


def test_a_retired_key_agent_keeps_the_60_s_check(monkeypatch, clock):
    _registered_key()
    calls: list = []
    monkeypatch.setattr(ak, "request_agent_token", _refusing("agent_retired", calls))
    for _ in range(5):
        standing.probe_once()
        clock.t += standing.PROBE_S
    assert len(calls) == 5                  # a restore must still take effect within a minute


# ── windy home pair sets a revoked key aside ────────────────────────────────────────────────────

def _keys_list(kid: str, status: str | None, *, http: int = 200):
    def handle(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bots/{PASSPORT}/keys"
        keys = [] if status is None else [{"kid": kid, "status": status}]
        return httpx.Response(http, json={"keys": keys})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("stop,status,http,revoked", [
    ("agent_key_revoked", "revoked", 200, True),
    ("agent_key_revoked", None, 200, True),          # not listed at all: not active
    ("agent_key_revoked", "active", 200, False),     # Eternitas still lists it active: keep refusing
    ("agent_key_revoked", "revoked", 503, False),    # unreadable list: not sure
    ("agent_retired", "revoked", 200, False),        # not a key stop
    (None, "revoked", 200, False),
])
def test_pair_recognises_only_a_key_eternitas_shows_revoked(stop, status, http, revoked):
    from windyfly import home_pair

    kid = _registered_key()
    if stop:
        standing.latch(stop, reason="move_aborted")
    assert home_pair._revoked_key_here(_keys_list(kid, status, http=http)) is revoked
    assert ak.credentials_path().exists()                     # the check itself never renames anything


def test_set_aside_renames_never_overwrites():
    from windyfly import home_pair

    kid = _registered_key()
    path = ak.credentials_path()
    stamp = home_pair.time.strftime('%Y%m%dT%H%M%SZ', home_pair.time.gmtime())
    clash = path.with_name(f"{path.name}.revoked-{stamp}")
    clash.write_text("an earlier set-aside")
    assert home_pair._set_aside_key() is True
    assert not path.exists() and clash.read_text() == "an earlier set-aside"     # never replaced
    kept = [p for p in path.parent.glob(f"{path.name}.revoked-*") if p != clash]
    assert len(kept) == 1 and json.loads(kept[0].read_text())["eternitas"]["keys"][0]["kid"] == kid


def _pair(monkeypatch, kid, *, confirm=True, cloud_passport=PASSPORT):
    from windyfly import home_pair, hub_hatch

    monkeypatch.setattr(hub_hatch, "cloud_agent", lambda: {"passport_number": cloud_passport, "where": "cloud"})
    monkeypatch.setattr(home_pair, "_load_agent_env", lambda: None)

    def confirm_fn(rec, report, **k):
        if not confirm:
            report("error", code="cancelled", say="  Cancelled. Nothing was changed.")
        return confirm
    monkeypatch.setattr(home_pair, "_confirm_cloud_link", confirm_fn)
    monkeypatch.setattr(ak, "begin_local_key", lambda **k: (_ for _ in ()).throw(KeyboardInterrupt()))  # stop after
    out = io.StringIO()
    try:
        rc = home_pair.pair(None, json_status=True, transport=_keys_list(kid, "revoked"), out=out)
    except KeyboardInterrupt:
        rc = "new-key"                                        # begin_local_key reached: the new pairing started
    return rc, [(e["event"], e.get("code")) for e in map(json.loads, out.getvalue().splitlines())]


def test_a_no_at_the_cloud_question_renames_nothing(monkeypatch):
    kid = _registered_key()
    standing.latch("agent_key_revoked", reason="move_aborted")
    rc, events = _pair(monkeypatch, kid, confirm=False)
    assert rc == 1 and events == [("error", "cancelled")]
    assert ak.has_registered_key()                            # "Nothing was changed." stays true


def test_another_agents_passport_is_refused_before_any_set_aside(monkeypatch):
    kid = _registered_key()
    standing.latch("agent_key_revoked", reason="move_aborted")
    rc, events = _pair(monkeypatch, kid, cloud_passport="ET26-OTHR-0001")
    assert rc == 1 and events == [("error", "another_agent_here")] and ak.has_registered_key()


def test_after_the_yes_the_key_is_set_aside_and_pairing_goes_on(monkeypatch):
    kid = _registered_key()
    standing.latch("agent_key_revoked", reason="move_aborted")
    rc, events = _pair(monkeypatch, kid)
    assert rc == "new-key" and events == [("progress", "revoked_key_set_aside")]
    assert not ak.has_registered_key()


@pytest.mark.parametrize("set_aside, end", [(False, "Nothing was changed."), (True, "Nothing else was changed.")])
def test_a_decline_after_a_set_aside_says_nothing_else(set_aside, end):
    from windyfly import home_pair

    said = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "denied"})))
    assert home_pair._poll(client, "https://connect.test", "dc", "kid", lambda e, say="", **f: said.append(say),
                           lambda s: None, lambda: 0.0, 100.0, 1.0,
                           nothing=home_pair._nothing_changed({"set_aside": set_aside})) is None
    assert said == [f"  The pairing was declined on your phone. {end}"]


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX link + unlink path")
def test_a_failed_remove_after_the_copy_takes_the_copy_back(monkeypatch, caplog):
    from windyfly import home_pair

    _registered_key()
    path = ak.credentials_path()
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == path:
            raise PermissionError("busy")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert home_pair._set_aside_key() is False
    assert path.exists() and not list(path.parent.glob(f"{path.name}.revoked-*"))
    assert "copied aside but could not be removed (PermissionError)" in caplog.text
