"""EPT self-refresh (Eternitas #159): decide, authenticate, persist, never leak."""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import threading
import time

import httpx
import pytest

from windyfly.eternitas import ept_refresh as er

PASSPORT = "ET26-TEST-AAAA"
DAY = 24 * 3600


def make_ept(*, exp_in: float, cvr: bool = True, sub: str = PASSPORT, extra: dict | None = None) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims = {"sub": sub, "exp": int(time.time() + exp_in), "iss": "eternitas.ai"}
    if cvr:
        claims["cvr"] = 3
    claims.update(extra or {})
    return f"{b64({'alg': 'ES256', 'typ': 'JWT'})}.{b64(claims)}.c2ln"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Never touch a real env file or a real hub session.
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    monkeypatch.delenv(er.ENV_KEY, raising=False)
    monkeypatch.delenv(er.PASSPORT_KEY, raising=False)
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    import windyfly.hub_login as hl
    monkeypatch.setattr(hl, "get_access_token", lambda *a, **k: None)
    yield


def env_file(tmp_path, token: str, mode: int = 0o600):
    p = tmp_path / "agent.env"
    p.write_text(f"TELEGRAM_BOT_TOKEN=keepme\n{er.ENV_KEY}={token}\nOTHER=1\n", encoding="utf-8")
    os.chmod(p, mode)
    return p


def transport(calls: list, *, status=200, new_token="", reissued=True, reason="stale_claims_version"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={
            "passport": PASSPORT, "ept_token": new_token, "expires_at": "2027-09-23T00:00:00Z",
            "reissued": reissued, "reissue_reason": reason,
        })
    return httpx.MockTransport(handler)


# ── when to call ─────────────────────────────────────────────────────

def test_no_call_when_current(monkeypatch):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=200 * DAY))
    calls: list = []
    assert er.refresh_ept(transport=transport(calls))["status"] == "current"
    assert calls == []


@pytest.mark.parametrize("token_kwargs,why", [
    ({"exp_in": 10 * DAY}, "expiring"),
    ({"exp_in": 200 * DAY, "cvr": False}, "stale_claims"),
])
def test_calls_when_due(monkeypatch, token_kwargs, why):
    monkeypatch.setenv(er.ENV_KEY, make_ept(**token_kwargs))
    assert er.needs_refresh(os.environ[er.ENV_KEY]) == (True, why)
    calls: list = []
    er.refresh_ept(transport=transport(calls, new_token=make_ept(exp_in=365 * DAY)))
    assert len(calls) == 1
    assert calls[0].url.path == f"/api/v1/bots/{PASSPORT}/ept/refresh"


def test_force_calls_even_when_current(monkeypatch):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=200 * DAY))
    calls: list = []
    er.refresh_ept(force=True, transport=transport(calls, new_token=make_ept(exp_in=365 * DAY)))
    assert len(calls) == 1


# ── which credential ─────────────────────────────────────────────────

def test_path_a_uses_own_valid_ept(monkeypatch):
    old = make_ept(exp_in=10 * DAY)
    monkeypatch.setenv(er.ENV_KEY, old)
    calls: list = []
    r = er.refresh_ept(transport=transport(calls, new_token=make_ept(exp_in=365 * DAY)))
    assert calls[0].headers["Authorization"] == f"Bearer {old}"
    assert r["via"] == "own_ept"


def test_path_b_uses_hub_login_when_expired(monkeypatch):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=-DAY))
    import windyfly.hub_login as hl
    monkeypatch.setattr(hl, "get_access_token", lambda *a, **k: "hub-access-token")
    calls: list = []
    r = er.refresh_ept(transport=transport(calls, new_token=make_ept(exp_in=365 * DAY)))
    assert calls[0].headers["Authorization"] == "Bearer hub-access-token"
    assert r["via"] == "owner_hub_login"


def test_needs_login_when_expired_and_no_session(monkeypatch):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=-DAY))
    calls: list = []
    assert er.refresh_ept(transport=transport(calls))["status"] == "needs_login"
    assert calls == []


def test_no_passport_means_no_call():
    calls: list = []
    assert er.refresh_ept(transport=transport(calls))["status"] == "no_passport"
    assert calls == []


@pytest.mark.parametrize("code", [401, 403])
def test_refusals_do_not_raise(monkeypatch, code):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=10 * DAY))
    r = er.refresh_ept(transport=transport([], status=code))
    assert r["status"] == "failed" and r["http"] == code
    assert "windy login" in r.get("hint", "")


def test_network_error_does_not_raise(monkeypatch):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=10 * DAY))

    def boom(request):
        raise httpx.ConnectError("down")

    assert er.refresh_ept(transport=httpx.MockTransport(boom))["status"] == "failed"


# ── persistence ──────────────────────────────────────────────────────

def test_updates_env_and_file_atomically(tmp_path, monkeypatch):
    old = make_ept(exp_in=10 * DAY)
    new = make_ept(exp_in=365 * DAY, extra={"windy_identity_id": "wid-1"})
    path = env_file(tmp_path, old, mode=0o640)
    monkeypatch.setenv("WINDY_ENV_FILE", str(path))
    monkeypatch.setenv(er.ENV_KEY, old)
    r = er.refresh_ept(transport=transport([], new_token=new))
    assert r["status"] == "refreshed" and r["persisted"] and r["has_windy_identity_id"]
    assert os.environ[er.ENV_KEY] == new
    text = path.read_text()
    assert f"{er.ENV_KEY}={new}\n" in text
    assert "TELEGRAM_BOT_TOKEN=keepme\n" in text and "OTHER=1\n" in text
    assert text.count(er.ENV_KEY) == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    backups = list(tmp_path.glob("agent.env.bak-ept-*"))
    assert len(backups) == 1 and old in backups[0].read_text()


def test_in_process_only_without_env_file(monkeypatch, caplog):
    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=10 * DAY))
    new = make_ept(exp_in=365 * DAY)
    with caplog.at_level(logging.INFO, logger="windyfly.eternitas.ept_refresh"):
        r = er.refresh_ept(transport=transport([], new_token=new))
    assert r["status"] == "refreshed" and r["persisted"] is False
    assert os.environ[er.ENV_KEY] == new
    assert "IN-PROCESS ONLY" in caplog.text and "WINDY_ENV_FILE" in caplog.text


def test_adopts_token_a_sibling_already_renewed(tmp_path, monkeypatch):
    stale = make_ept(exp_in=10 * DAY)
    fresh = make_ept(exp_in=365 * DAY)
    path = env_file(tmp_path, fresh)            # another process wrote this
    monkeypatch.setenv("WINDY_ENV_FILE", str(path))
    monkeypatch.setenv(er.ENV_KEY, stale)       # this process started earlier
    calls: list = []
    r = er.refresh_ept(transport=transport(calls))
    assert r["status"] == "adopted" and calls == []
    assert os.environ[er.ENV_KEY] == fresh


def test_never_logs_tokens(tmp_path, monkeypatch, caplog):
    old = make_ept(exp_in=10 * DAY)
    new = make_ept(exp_in=365 * DAY)
    path = env_file(tmp_path, old)
    monkeypatch.setenv("WINDY_ENV_FILE", str(path))
    monkeypatch.setenv(er.ENV_KEY, old)
    with caplog.at_level(logging.DEBUG):
        er.refresh_ept(transport=transport([], new_token=new))
        er.refresh_ept(force=True, transport=transport([], status=403))
    for secret in (old, new, old.split(".")[1], new.split(".")[1]):
        assert secret not in caplog.text


# ── wiring ───────────────────────────────────────────────────────────

def test_boot_step_does_not_block(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return {"status": "current"}

    monkeypatch.setattr(er, "refresh_ept", slow)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from windyfly.agent.boot import _step_refresh_ept

    t0 = time.monotonic()
    _step_refresh_ept(None)
    assert time.monotonic() - t0 < 0.5
    assert started.wait(2)
    release.set()


def test_boot_sequence_and_daily_job_registered():
    from windyfly.agent.boot import default_capability_registration_sequence
    from windyfly.agent.maintenance import default_jobs

    steps = {s.name: s for s in default_capability_registration_sequence()}
    assert steps["eternitas.ept_refresh"].optional is True
    assert "eternitas.ept_refresh.daily" in {j.name for j in default_jobs({})}


def test_background_refresh_skipped_under_pytest_and_opt_out(monkeypatch):
    monkeypatch.setattr(er, "refresh_ept", lambda *a, **k: pytest.fail("must not run"))
    assert er.refresh_in_background() is None          # PYTEST_CURRENT_TEST is set
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("WINDY_DISABLE_EPT_REFRESH", "1")
    assert er.refresh_in_background() is None
