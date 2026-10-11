"""The agent's own Matrix id comes from the token, not only from MATRIX_BOT_USER.

Windy 0 (and every quickstart install) has no MATRIX_BOT_USER, so teams' pair rooms said
"chat is not configured" and phone tools had no sender id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from windyfly.channels import matrix_self
from windyfly.channels.matrix_self import own_matrix_id, set_confirmed

_ME = "@agent_et26-test-m0nf:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    set_confirmed(None)
    for key in ("MATRIX_BOT_USER", "MATRIX_HOMESERVER", "MATRIX_BOT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    yield
    set_confirmed(None)


def _whoami(status: int = 200, user_id: str | None = _ME) -> MagicMock:
    resp = MagicMock(status_code=status)
    resp.json.return_value = {"user_id": user_id} if user_id else {}
    return resp


def test_confirmed_id_wins_without_network(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    set_confirmed(_ME)
    with patch.object(matrix_self.httpx, "get", side_effect=AssertionError("no network")):
        assert own_matrix_id() == _ME


def test_whoami_answers_and_is_cached(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test/")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    with patch.object(matrix_self.httpx, "get", return_value=_whoami()) as get:
        assert own_matrix_id() == _ME
        assert own_matrix_id() == _ME
    assert get.call_count == 1
    url = get.call_args.args[0]
    assert url == "https://chat.example.test/_matrix/client/v3/account/whoami"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}


@pytest.mark.parametrize("failure", [httpx.ConnectError("down"), _whoami(401, None)])
def test_whoami_failing_is_empty_even_with_env_set(monkeypatch, failure):
    """Never the configured env id: it can be stale (Hub: fail closed)."""
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    monkeypatch.setenv("MATRIX_BOT_USER", "@configured:chat.example.test")
    kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with patch.object(matrix_self.httpx, "get", **kwargs):
        assert own_matrix_id() == ""


def test_import_makes_no_network_call():
    import importlib

    with patch("httpx.get", side_effect=AssertionError("network at import")):
        importlib.reload(matrix_self)
    matrix_self.set_confirmed(None)


def test_nothing_known_is_empty(monkeypatch):
    monkeypatch.setenv("MATRIX_BOT_USER", "@configured:chat.example.test")
    assert own_matrix_id() == ""


def test_settle_pair_room_refuses_without_own_id(monkeypatch):
    from windyfly.agent import teams

    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    with patch.object(matrix_self.httpx, "get", side_effect=httpx.ConnectError("down")), \
         patch.object(teams, "_mx", side_effect=AssertionError("no Matrix call")):
        reason = teams._settle_pair_room("!r:chat.example.test", {"invite": ["@partner:x"]})
    assert reason == "chat is not configured for this agent"


def test_pair_room_without_matrix_bot_user_uses_the_token_identity(monkeypatch):
    from windyfly.agent import teams

    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    calls: list[tuple] = []

    def fake_mx(method, path, body=None):
        calls.append((method, path, body))
        return 200, {"room_id": "!r:chat.example.test"}

    with patch.object(matrix_self.httpx, "get", return_value=_whoami()), \
         patch.object(teams, "_mx", side_effect=fake_mx):
        room, err = teams._create_pair_room(
            {"claim": "c1", "name": "pair", "owner_mxid": "@owner:chat.example.test"})
    assert err is None and room == "!r:chat.example.test"
    users = calls[0][2]["power_level_content_override"]["users"]
    assert users == {"@owner:chat.example.test": 100, _ME: 100}


@pytest.mark.asyncio
async def test_matrix_login_publishes_the_confirmed_id(monkeypatch):
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from tests.test_matrix_bot import _make_config

    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    config = _make_config()
    config["matrix"].pop("bot_user", None)
    db = Database(":memory:")
    bot = WindyFlyMatrixBot(config, db, WriteQueue())
    bot.client.whoami = AsyncMock(return_value=MagicMock(user_id=_ME))
    with patch("windyfly.chat_session.fetch_agent_chat_session", new=AsyncMock(return_value=None)):
        await bot.login()
    with patch.object(matrix_self.httpx, "get", side_effect=AssertionError("no network")):
        assert own_matrix_id() == _ME
    db.close()


def test_failed_whoami_is_not_retried_for_30_seconds(monkeypatch):
    """Hub (#513 note): a down homeserver costs one 5 s wait per 30 s, not one per call."""
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    clock = [1000.0]
    monkeypatch.setattr(matrix_self.time, "monotonic", lambda: clock[0])
    with patch.object(matrix_self.httpx, "get", side_effect=httpx.ConnectError("down")) as get:
        assert own_matrix_id() == ""
        clock[0] += 29.0
        assert own_matrix_id() == ""
        assert get.call_count == 1
        clock[0] += 2.0
        assert own_matrix_id() == ""
        assert get.call_count == 2


def test_login_clears_a_cached_failure(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    with patch.object(matrix_self.httpx, "get", side_effect=httpx.ConnectError("down")):
        assert own_matrix_id() == ""
    set_confirmed(_ME)
    with patch.object(matrix_self.httpx, "get", side_effect=AssertionError("no network")):
        assert own_matrix_id() == _ME
