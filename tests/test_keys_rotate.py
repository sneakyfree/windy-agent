"""Wave 10: tests for `windy keys rotate` + `windy keys show`.

The auto-rotation path is already covered in test_hatch_orchestrator and
elsewhere. This file pins the *manual* CLI surface: that rotate is
idempotent, abortable, and that revoke failures don't mask a successful
mint."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from windyfly.auth.bot_credentials import BotCredential
from windyfly.commands import keys as keys_cmd


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Redirect the bot-key cache to a tmp file so tests don't stomp ~/data."""
    import windyfly.auth.bot_credentials as bc
    cache = tmp_path / "bot_key.json"
    monkeypatch.setattr(bc, "_CACHE_FILE", cache)
    return cache


@pytest.fixture(autouse=True)
def creds_env(monkeypatch):
    monkeypatch.setenv("WINDY_JWT", "owner_jwt_test")
    monkeypatch.setenv("WINDY_PRO_URL", "https://pro.test")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-ROT-TEST")
    for key in ("WINDYMAIL_API_URL", "WINDY_CLOUD_URL", "MATRIX_HOMESERVER"):
        monkeypatch.delenv(key, raising=False)


def _fresh_cred(key_id: str, bot_key: str = "wk_fresh", days: int = 90) -> BotCredential:
    return BotCredential(
        bot_key=bot_key,
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
        windy_identity_id="wi_test",
        passport_number="ET26-ROT-TEST",
        key_id=key_id,
        scopes=["mail:send", "cloud:upload"],
    )


def test_rotate_mints_and_revokes(monkeypatch, tmp_cache) -> None:
    # Seed an "old" cached key.
    old = _fresh_cred(key_id="key_old", bot_key="wk_old", days=10)
    tmp_cache.write_text(
        __import__("json").dumps(old.to_dict())
    )

    new_cred = _fresh_cred(key_id="key_new", bot_key="wk_new", days=90)
    async def _fake_mint(**_kw):
        # mint_bot_key writes the cache as a side effect. Mirror that here
        # so _load_cached returns the new cred after this runs.
        tmp_cache.write_text(__import__("json").dumps(new_cred.to_dict()))
        return new_cred

    revokes: list[dict] = []
    async def _fake_revoke(**kw):
        revokes.append(kw)
        return {"revoked": True, "cascade": {}}

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)
    monkeypatch.setattr(keys_cmd, "_revoke", _fake_revoke)

    keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))

    # Mint ran; revoke saw the OLD key_id; cache now has the new key.
    assert revokes and revokes[0]["key_id"] == "key_old"
    from windyfly.auth.bot_credentials import _load_cached  # type: ignore[attr-defined]
    final = _load_cached()
    assert final is not None and final.key_id == "key_new"


def test_rotate_idempotent_no_previous_key(monkeypatch, tmp_cache) -> None:
    """First-run rotate — no cached key yet — should mint without revoking."""
    new_cred = _fresh_cred(key_id="key_first", bot_key="wk_first")

    async def _fake_mint(**_kw):
        tmp_cache.write_text(__import__("json").dumps(new_cred.to_dict()))
        return new_cred

    revoke_called: list[int] = []
    async def _fake_revoke(**_kw):
        revoke_called.append(1)
        return {"revoked": True, "cascade": {}}

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)
    monkeypatch.setattr(keys_cmd, "_revoke", _fake_revoke)

    keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))
    assert not revoke_called, "Nothing to revoke on a first-ever mint"


def test_rotate_skips_revoke_when_same_key_id(monkeypatch, tmp_cache) -> None:
    """If the server returns the same key_id (idempotent re-mint), we must
    not try to revoke — that would self-invalidate the active key."""
    same = _fresh_cred(key_id="key_stable", bot_key="wk_stable")
    tmp_cache.write_text(__import__("json").dumps(same.to_dict()))

    async def _fake_mint(**_kw):
        tmp_cache.write_text(__import__("json").dumps(same.to_dict()))
        return same

    revoke_called: list[int] = []
    async def _fake_revoke(**_kw):
        revoke_called.append(1)
        return {"revoked": True, "cascade": {}}

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)
    monkeypatch.setattr(keys_cmd, "_revoke", _fake_revoke)

    keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))
    assert not revoke_called, "Same key_id must skip revoke (idempotent)"


def test_rotate_tolerates_revoke_failure(monkeypatch, tmp_cache) -> None:
    """A failing revoke must not roll back the mint — the new key is
    already live and useful; the old key will naturally expire."""
    old = _fresh_cred(key_id="key_old", bot_key="wk_old", days=5)
    tmp_cache.write_text(__import__("json").dumps(old.to_dict()))
    new = _fresh_cred(key_id="key_new", bot_key="wk_new")

    async def _fake_mint(**_kw):
        tmp_cache.write_text(__import__("json").dumps(new.to_dict()))
        return new

    async def _fake_revoke(**_kw):
        raise RuntimeError("pro broker 503")

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)
    monkeypatch.setattr(keys_cmd, "_revoke", _fake_revoke)

    # Must NOT raise / exit; the function should swallow the revoke error.
    keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))

    from windyfly.auth.bot_credentials import _load_cached  # type: ignore[attr-defined]
    final = _load_cached()
    assert final is not None and final.key_id == "key_new"


def test_rotate_abort_via_keyboard_interrupt_exits_130(monkeypatch, tmp_cache) -> None:
    """Ctrl-C during mint must exit 130 (shell signal convention) and
    leave the old cached key untouched."""
    old = _fresh_cred(key_id="key_old")
    tmp_cache.write_text(__import__("json").dumps(old.to_dict()))

    async def _fake_mint(**_kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)

    with pytest.raises(SystemExit) as excinfo:
        keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))
    assert excinfo.value.code == 130

    from windyfly.auth.bot_credentials import _load_cached  # type: ignore[attr-defined]
    still = _load_cached()
    assert still is not None and still.key_id == "key_old", (
        "Abort before mint must leave the old key in place"
    )


def test_rotate_requires_jwt(monkeypatch, tmp_cache) -> None:
    """Without WINDY_JWT we can't authorize the mint; exit 2 with a hint."""
    monkeypatch.delenv("WINDY_JWT", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))
    assert excinfo.value.code == 2


def test_rotate_requires_passport(monkeypatch, tmp_cache) -> None:
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        keys_cmd.cmd_keys(Namespace(action="rotate", hard=False))
    assert excinfo.value.code == 2


def test_rotate_hard_cascades_to_mail_and_cloud(monkeypatch, tmp_cache) -> None:
    """--hard must pass the mail/cloud webhook URLs through to revoke_bot_key."""
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.test")
    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.test")
    old = _fresh_cred(key_id="key_old")
    tmp_cache.write_text(__import__("json").dumps(old.to_dict()))
    new = _fresh_cred(key_id="key_new")

    async def _fake_mint(**_kw):
        tmp_cache.write_text(__import__("json").dumps(new.to_dict()))
        return new

    captured: dict = {}
    async def _fake_revoke(**kw):
        captured.update(kw)
        return {"revoked": True, "cascade": {url: 200 for url in kw["cascade"] or []}}

    monkeypatch.setattr(keys_cmd, "_mint", _fake_mint)
    monkeypatch.setattr(keys_cmd, "_revoke", _fake_revoke)

    keys_cmd.cmd_keys(Namespace(action="rotate", hard=True))

    cascade = captured.get("cascade") or []
    assert any("mail.test" in u for u in cascade)
    assert any("cloud.test" in u for u in cascade)


def test_show_reports_cached_key(monkeypatch, tmp_cache, capsys) -> None:
    cred = _fresh_cred(key_id="key_shown", days=45)
    tmp_cache.write_text(__import__("json").dumps(cred.to_dict()))

    keys_cmd.cmd_keys(Namespace(action="show"))
    out = capsys.readouterr().out
    assert "key_shown" in out
    assert "ET26-ROT-TEST" in out


def test_show_reports_no_key(tmp_cache, capsys) -> None:
    # tmp_cache fixture ensures path exists, but no file yet.
    keys_cmd.cmd_keys(Namespace(action="show"))
    out = capsys.readouterr().out
    assert "No cached" in out
