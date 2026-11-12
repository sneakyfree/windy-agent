"""An agent that holds its own key never sends its owner's Windy sign-in (WINDY_JWT) to Windy Cloud, and every
fall back to the old Cloud credential is counted.

Measured on Windy 0 (BOARD 2026-11-11T11:33:50Z): backups mint the agent's own token first, but when that mint
failed (a 429) the old credential went instead, and on that computer the old credential was the person's sign-in.
The once-per-reason log line hid how often. DNA strand plan §1.5: the agent never holds the owner's credentials.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from windyfly import cloud_backup as cb
from windyfly.agent import cloud_auth, service_auth
from windyfly.eternitas import agent_keys


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WINDY_CLOUD_TOKEN", "WINDY_JWT"):
        monkeypatch.delenv(k, raising=False)
    cloud_auth.fallbacks.clear()
    yield
    cloud_auth.fallbacks.clear()


def _key(monkeypatch, has: bool) -> None:
    monkeypatch.setattr(agent_keys, "has_registered_key", lambda *a, **k: has)


# ── which old credential ────────────────────────────────────────────────────────────────────────────────────

def test_the_cloud_token_wins_whatever_else_is_set(monkeypatch):
    _key(monkeypatch, True)
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "svc-token")
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    assert cloud_auth.old_credential() == "svc-token" == cb._get_cloud_token()


def test_an_agent_with_its_own_key_never_gets_the_person_s_sign_in(monkeypatch):
    _key(monkeypatch, True)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    assert cloud_auth.old_credential() == "" == cb._get_cloud_token()


def test_an_agent_with_no_key_keeps_the_old_road(monkeypatch):
    _key(monkeypatch, False)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    assert cloud_auth.old_credential() == "person-signin"


def test_a_key_file_that_cannot_be_read_is_no_reason_to_send_the_sign_in(monkeypatch):
    def broken(*a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(agent_keys, "has_registered_key", broken)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    assert cloud_auth.old_credential() == ""


# ── the backup still runs on the agent's own token ──────────────────────────────────────────────────────────

def test_the_scheduled_backup_is_not_skipped_when_only_the_agent_s_own_token_exists(monkeypatch, tmp_path):
    _key(monkeypatch, True)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")
    monkeypatch.setattr(service_auth, "agent_headers", lambda aud, m, u: {"Authorization": "DPoP own", "DPoP": "p"})
    monkeypatch.setattr(cb, "should_backup", lambda config=None: True)
    monkeypatch.setattr(cb, "_BACKUP_STATE_FILE", tmp_path / "backup_state.json")
    ran = []

    async def fake_upload(config=None, *, suspect=False):
        ran.append(suspect)
        return {"success": True}

    monkeypatch.setattr(cb, "backup_to_cloud", fake_upload)
    monkeypatch.setattr(cb, "episode_count", lambda *a, **k: 0)
    asyncio.run(cb.run_backup_if_due({"memory": {"db_path": str(tmp_path / "m.db")}}))
    assert ran == [False]


def test_with_no_credential_at_all_the_scheduled_backup_is_skipped(monkeypatch, tmp_path):
    _key(monkeypatch, True)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")

    def no_token(*a):
        raise service_auth.ServiceAuthError("http_429")

    monkeypatch.setattr(service_auth, "agent_headers", no_token)
    monkeypatch.setattr(cb, "should_backup", lambda config=None: True)
    ran = []

    async def fake_upload(config=None, *, suspect=False):
        ran.append(suspect)
        return {"success": True}

    monkeypatch.setattr(cb, "backup_to_cloud", fake_upload)
    assert asyncio.run(cb.run_backup_if_due({})) is None
    assert ran == []                                   # it waits for its own token; it never borrows the sign-in


def test_the_old_header_carries_no_sign_in_for_an_agent_with_a_key(monkeypatch):
    """What the backup sends when its own mint fails and no bot key is cached: nothing, not the person's sign-in."""
    from windyfly.auth.bot_credentials import ecosystem_auth_header

    _key(monkeypatch, True)
    monkeypatch.setenv("WINDY_JWT", "person-signin")
    monkeypatch.setattr("windyfly.auth.bot_credentials.get_bot_key", _no_bot_key)
    assert asyncio.run(ecosystem_auth_header(fallback_token=cb._get_cloud_token())) == {}


async def _no_bot_key(*a, **k):
    return None


# ── every fallback is counted, never the token ──────────────────────────────────────────────────────────────

def test_every_mint_failure_is_logged_with_its_count(monkeypatch, caplog):
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")

    def no_token(*a):
        raise service_auth.ServiceAuthError("http_429")

    monkeypatch.setattr(service_auth, "agent_headers", no_token)
    with caplog.at_level(logging.INFO, logger="windyfly.agent.cloud_auth"):
        for _ in range(3):
            assert cloud_auth.agent_headers("POST", "https://cloud.test/api/v1/archive/agent") is None
    lines = [r.getMessage() for r in caplog.records if "[cloud-auth]" in r.getMessage()]
    assert len(lines) == 3 and lines[-1].endswith("3 time(s) for this reason")
    assert cloud_auth.fallbacks == {"no_token:http_429": 3}


def test_every_refusal_retry_is_counted(caplog):
    with caplog.at_level(logging.INFO, logger="windyfly.agent.cloud_auth"):
        cloud_auth.fallback_used("backup upload", 401)
        cloud_auth.fallback_used("backup upload", 401)
        cloud_auth.fallback_used("backup list", 403)
    assert cloud_auth.fallbacks == {"refused:401": 2, "refused:403": 1}
    assert "2 time(s)" in caplog.records[1].getMessage()


def test_no_token_ever_reaches_a_log_line(monkeypatch, caplog):
    _key(monkeypatch, False)
    monkeypatch.setenv("WINDY_JWT", "person-signin-SECRET")
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")
    monkeypatch.setattr(service_auth, "agent_headers",
                        lambda *a: (_ for _ in ()).throw(service_auth.ServiceAuthError("no_key")))
    with caplog.at_level(logging.DEBUG):
        cloud_auth.agent_headers("GET", "https://cloud.test/x")
        cloud_auth.old_credential()
        cloud_auth.fallback_used("backup", 401)
    assert "SECRET" not in caplog.text
