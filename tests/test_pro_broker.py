"""Wave 8: Pro managed-credential detection in ``windy go``.

Covers ``pro_broker.py`` — the module that exchanges a local Pro
account token for a short-lived LLM credential — and the --byok
escape hatch wired into the quickstart CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from windyfly import pro_broker
from windyfly.pro_broker import (
    BrokeredCredential,
    default_pro_config_path,
    fetch_broker_credential,
    has_valid_pro_token,
    read_pro_config,
)


# ─── read_pro_config ──────────────────────────────────────────────


def test_read_pro_config_missing(tmp_path: Path) -> None:
    assert read_pro_config(tmp_path / "nope.json") is None


def test_read_pro_config_malformed(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    assert read_pro_config(p) is None


def test_read_pro_config_non_object(tmp_path: Path) -> None:
    p = tmp_path / "arr.json"
    p.write_text('["unexpected array"]')
    assert read_pro_config(p) is None


def test_read_pro_config_happy(tmp_path: Path) -> None:
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"account_token": "pro_abc", "base_url": "http://localhost:8098"}))
    cfg = read_pro_config(p)
    assert cfg == {"account_token": "pro_abc", "base_url": "http://localhost:8098"}


# ─── has_valid_pro_token ──────────────────────────────────────────


def test_has_valid_pro_token_true(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"account_token": "pro_abc"}))
    assert has_valid_pro_token(p) is True


def test_has_valid_pro_token_empty(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"account_token": ""}))
    assert has_valid_pro_token(p) is False


def test_has_valid_pro_token_alt_key_name(tmp_path: Path) -> None:
    """'token' is accepted as a fallback alias for account_token."""
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"token": "pro_legacy"}))
    assert has_valid_pro_token(p) is True


def test_has_valid_pro_token_missing(tmp_path: Path) -> None:
    assert has_valid_pro_token(tmp_path / "nope.json") is False


# ─── fetch_broker_credential ─────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, raise_on_json: bool = False):
        self.status_code = status_code
        self._json = json_body or {}
        self._raise_on_json = raise_on_json

    def json(self) -> dict:
        if self._raise_on_json:
            raise ValueError("not JSON")
        return self._json


class _FakeClient:
    """httpx-shaped test double capturing the last request."""

    def __init__(self, response: _FakeResponse):
        self.response = response
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_json: dict | None = None

    def post(self, url: str, headers=None, json=None):  # noqa: A002
        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        return self.response

    def close(self) -> None:
        pass


def test_fetch_broker_credential_happy(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({
        "account_token": "pro_abc",
        "base_url": "http://pro.local:8098",
    }))
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    client = _FakeClient(_FakeResponse(200, {
        "provider": "anthropic",
        "api_key": "wk_broker_xxx",
        "model": "claude-3-5-sonnet-latest",
        "expires_at": expires,
    }))

    cred = fetch_broker_credential(config_path=cfg, http_client=client)

    assert isinstance(cred, BrokeredCredential)
    assert cred.provider == "anthropic"
    assert cred.env_var == "ANTHROPIC_API_KEY"
    assert cred.api_key == "wk_broker_xxx"
    assert cred.model == "claude-3-5-sonnet-latest"
    assert cred.is_expired is False

    # Request must go to Pro's broker endpoint with the account token
    # in the Authorization header.
    assert client.last_url == "http://pro.local:8098/api/v1/broker/llm-credentials"
    assert client.last_headers == {"Authorization": "Bearer pro_abc"}
    assert client.last_json == {"purpose": "hatch"}


def test_fetch_broker_credential_no_config(tmp_path: Path) -> None:
    assert fetch_broker_credential(config_path=tmp_path / "missing.json") is None


def test_fetch_broker_credential_pro_5xx(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"account_token": "pro_abc"}))
    client = _FakeClient(_FakeResponse(503))
    assert fetch_broker_credential(config_path=cfg, http_client=client) is None


def test_fetch_broker_credential_unknown_provider(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"account_token": "pro_abc"}))
    client = _FakeClient(_FakeResponse(200, {
        "provider": "fictional_llm",
        "api_key": "x",
        "model": "m",
    }))
    assert fetch_broker_credential(config_path=cfg, http_client=client) is None


def test_fetch_broker_credential_missing_fields(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"account_token": "pro_abc"}))
    client = _FakeClient(_FakeResponse(200, {"provider": "openai"}))  # missing api_key
    assert fetch_broker_credential(config_path=cfg, http_client=client) is None


def test_brokered_credential_expired() -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    cred = BrokeredCredential(
        provider="openai", env_var="OPENAI_API_KEY",
        api_key="k", model="m", expires_at=past,
    )
    assert cred.is_expired is True


def test_default_pro_config_path_under_home() -> None:
    assert default_pro_config_path() == Path.home() / ".windypro" / "config.json"


# ─── quickstart --byok wiring ────────────────────────────────────


def test_quickstart_byok_flag_skips_pro_broker(monkeypatch) -> None:
    """With --byok, the quickstart must NOT call Pro's broker even if a
    valid Pro config is sitting on disk."""
    from argparse import Namespace
    import windyfly.quickstart as qs

    calls: list[str] = []
    monkeypatch.setattr(
        qs, "_try_pro_broker",
        lambda args: calls.append("tried") or True,
    )
    # Stop the flow before it tries to install deps or launch.
    monkeypatch.setattr(qs, "_install_deps", lambda: None)
    monkeypatch.setattr(qs, "_launch", lambda args: None)
    monkeypatch.setattr(qs, "_install_prereqs", lambda missing: None)
    monkeypatch.setattr(qs, "can_run", lambda cmd: True)
    # Force the "no config yet" + "no env key" + "no clipboard" path so
    # we actually reach the pro-broker check (or would, without --byok).
    monkeypatch.setattr(qs, "read_clipboard", lambda: None)
    monkeypatch.setattr(qs, "Confirm", type("C", (), {"ask": staticmethod(lambda *a, **k: False)}))

    args = Namespace(key=None, byok=True, model=None, preset=None, no_browser=True)

    # Use a Prompt that always returns a sentinel so we exit quickly
    # after the provider-menu step without mucking with real I/O.
    monkeypatch.setattr(
        qs, "Prompt",
        type("P", (), {"ask": staticmethod(lambda *a, **k: "exit")}),
    )

    # We only care that _try_pro_broker was NOT called. The rest of
    # the flow can bail however it likes.
    try:
        qs.cmd_go(args)
    except Exception:
        pass

    assert calls == [], "Pro broker must be skipped when --byok is set"


def test_quickstart_pro_broker_short_circuit(monkeypatch) -> None:
    """Without --byok, _try_pro_broker returning True should end cmd_go.

    i.e. Pro-credential detection takes precedence over the paste flow."""
    from argparse import Namespace
    import windyfly.quickstart as qs

    calls: list[str] = []
    monkeypatch.setattr(qs, "_install_deps", lambda: calls.append("install_deps"))
    monkeypatch.setattr(qs, "_launch", lambda args: calls.append("launch"))
    monkeypatch.setattr(qs, "_install_prereqs", lambda missing: None)
    monkeypatch.setattr(qs, "can_run", lambda cmd: True)
    monkeypatch.setattr(qs, "_try_pro_broker", lambda args: calls.append("pro") or True)
    # Paste-flow fallbacks should NOT run.
    monkeypatch.setattr(qs, "read_clipboard", lambda: calls.append("clipboard") or None)

    args = Namespace(key=None, byok=False, model=None, preset=None, no_browser=True)
    qs.cmd_go(args)

    assert "pro" in calls, "Pro broker path should be attempted"
    assert "clipboard" not in calls, \
        "Once Pro broker succeeds, cmd_go must return before clipboard check"
