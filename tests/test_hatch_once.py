"""One agent, one passport — and no pretend inbox.

Found by the read-only hatch audit (2026-09-23):
  A. The hatch kept ETERNITAS_PASSPORT only in the process env, and
     re-running `windy go` rewrote .env from scratch (blanking the EPT too),
     so the next hatch minted a SECOND passport for the same agent.
  B. `windy go` passed no config, so the mail step fell into the local
     MockMailServer and reported "✓ Windy Mail — <name>@windymail.ai" for an
     inbox that exists nowhere.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from windyfly import quickstart
from windyfly.hatch_orchestrator import HatchResult, _step_mail, orchestrate_hatch
from windyfly.memory.database import Database

ETERNITAS_BASE = "https://api.eternitas.test"


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = quickstart.PROJECT_ROOT  # conftest's per-test project root
    monkeypatch.setenv("WINDYFLY_HOME", str(root))
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    for var in ("ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN", "WINDY_JWT",
                "_WINDYFLY_FORCE_HATCH", "OWNER_PHONE", "WINDYMAIL_SERVICE_TOKEN"):
        # setenv first so monkeypatch records the original and undoes the
        # hatch's own os.environ writes at teardown.
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    return root


def _env(root) -> dict[str, str]:
    return quickstart._read_env_values(root / ".env")


# ── A. the passport number survives, and `windy go` won't mint twice ──

@respx.mock
def test_hatch_persists_the_passport_number(home, db, monkeypatch):
    monkeypatch.setenv("ETERNITAS_URL", ETERNITAS_BASE)
    respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
        return_value=httpx.Response(201, json={
            "passport": "ET26-ONCE-0001", "name": "Once", "ept_token": "jwt.tok.sig",
            "status": "active", "trust_score": 70,
        })
    )
    respx.route(host="api.eternitas.test").mock(return_value=httpx.Response(404))
    monkeypatch.setattr(
        "windyfly.hatch_orchestrator._ensure_hatch_sign_in", lambda: None
    )
    asyncio.run(orchestrate_hatch("Once", db=db))
    env = _env(home)
    assert env.get("ETERNITAS_PASSPORT") == "ET26-ONCE-0001"
    assert env.get("ETERNITAS_PASSPORT_TOKEN") == "jwt.tok.sig"


def test_rewriting_config_keeps_the_identity(home):
    (home / ".env").write_text(
        "ETERNITAS_PASSPORT=ET26-KEEP-0001\nETERNITAS_PASSPORT_TOKEN=jwt.keep.sig\n"
    )
    quickstart.write_keyless_config()
    env = _env(home)
    assert env["ETERNITAS_PASSPORT"] == "ET26-KEEP-0001"
    assert env["ETERNITAS_PASSPORT_TOKEN"] == "jwt.keep.sig"


def test_go_refuses_to_hatch_an_agent_that_has_a_passport(home, monkeypatch, capsys):
    (home / ".env").write_text("ETERNITAS_PASSPORT=ET26-KEEP-0001\n")

    def no_hatch(**_k):
        raise AssertionError("must not mint a second passport")

    monkeypatch.setattr("windyfly.hatch_orchestrator.run_hatch", no_hatch)
    quickstart._try_hatch_provisioning(non_interactive=True)
    out = capsys.readouterr().out
    assert "already has a passport (ET26-KEEP-0001)" in out
    assert "windy deregister" in out and "--force" in out


def test_go_force_hatches_a_fresh_identity(home, monkeypatch):
    (home / ".env").write_text("ETERNITAS_PASSPORT=ET26-OLD-0001\n")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-OLD-0001")
    monkeypatch.setenv("_WINDYFLY_FORCE_HATCH", "1")
    seen = {}

    def fake_run_hatch(**_k):
        import os
        seen["adopt"] = os.environ.get("ETERNITAS_PASSPORT")
        raise RuntimeError("stop here")

    monkeypatch.setattr("windyfly.hatch_orchestrator.run_hatch", fake_run_hatch)
    monkeypatch.setattr("windyfly.hatching.play_hatching", lambda **_k: None)
    quickstart._try_hatch_provisioning(non_interactive=True)
    assert seen == {"adopt": None}   # not adopting the old number


def test_mock_tokens_do_not_count_as_a_passport(home):
    (home / ".env").write_text("ETERNITAS_PASSPORT_TOKEN=mock-abc\n")
    assert quickstart.existing_passport() == ""


# ── B. no mock inbox outside an explicit dev/test opt-in ─────────────

def test_mail_mock_needs_the_explicit_optin(db, monkeypatch):
    monkeypatch.delenv("WINDYFLY_ALLOW_FAKE_IDENTITY", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.delenv("WINDYMAIL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", raising=False)
    result = HatchResult(passport_id="ET26-MAIL-0001")
    asyncio.run(_step_mail(result, "Fly", db))
    assert result.mail_provisioned is False
    assert result.mail_is_mock is False
    assert not result.email_address


def test_mail_mock_still_available_to_tests_and_dev(db, monkeypatch):
    monkeypatch.setenv("WINDYFLY_ALLOW_FAKE_IDENTITY", "1")
    result = HatchResult(passport_id="ET26-MAIL-0002")
    asyncio.run(_step_mail(result, "Fly", db))
    assert result.mail_is_mock is True


def test_go_hands_the_hatch_its_config(home, monkeypatch):
    quickstart.write_keyless_config()
    seen = {}

    def fake_run_hatch(**kw):
        seen["config"] = kw.get("config")
        raise RuntimeError("stop here")

    monkeypatch.setattr("windyfly.hatch_orchestrator.run_hatch", fake_run_hatch)
    monkeypatch.setattr("windyfly.hatching.play_hatching", lambda **_k: None)
    quickstart._try_hatch_provisioning(non_interactive=True)
    assert isinstance(seen["config"], dict)
