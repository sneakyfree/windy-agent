"""Wave 8: Pro managed-credential detection in ``windy go``.

Covers ``pro_broker.py`` — the module that exchanges a local Pro
account token for a short-lived LLM credential — and the --byok
escape hatch wired into the quickstart CLI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from windyfly import pro_broker
from windyfly.pro_broker import (
    BrokeredCredential,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_SCOPE,
    default_pro_config_path,
    fetch_broker_credential,
    has_valid_pro_token,
    read_pro_config,
    sign_broker_request,
)

# Constant used across tests — Pro and agent both hold this in prod.
SIGNING_SECRET = "shared-broker-secret-for-tests"


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


def test_has_valid_pro_token_windy_identity_id_only(tmp_path: Path) -> None:
    """Post-HMAC migration, windy_identity_id alone is enough."""
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"windy_identity_id": "wi_post_migration"}))
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
    """httpx-shaped test double capturing the last request.

    Accepts ``content=`` (raw bytes) + ``headers=`` because that's the
    shape the HMAC flow uses now. Stores both for signature assertions.
    """

    def __init__(self, response: _FakeResponse):
        self.response = response
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_content: bytes | None = None

    def post(self, url: str, content=None, headers=None, json=None):  # noqa: A002
        self.last_url = url
        self.last_headers = headers
        self.last_content = content
        # The real flow never uses json=, but tolerate it so tests that
        # accidentally pass it don't silently drop bytes.
        if content is None and json is not None:
            import json as _json
            self.last_content = _json.dumps(json).encode("utf-8")
        return self.response

    def close(self) -> None:
        pass


def _valid_pro_cfg(tmp_path: Path) -> Path:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({
        "windy_identity_id": "wi_abc",
        "base_url":          "http://pro.local:8098",
        "passport_number":   "ET26-ABC-DEF",
    }))
    return cfg


def test_sign_broker_request_layout_matches_trust_webhook() -> None:
    """Signature layout must be `sha256=<hex>` to stay symmetric with
    the inbound trust.verify.verify_hmac side of the HMAC contract."""
    body = b'{"a":1}'
    sig = sign_broker_request(body, SIGNING_SECRET)
    assert sig.startswith("sha256=")
    expected = hmac.new(SIGNING_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_fetch_broker_credential_happy(tmp_path: Path) -> None:
    cfg = _valid_pro_cfg(tmp_path)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    client = _FakeClient(_FakeResponse(200, {
        "broker_token":     "wk_broker_xxx",
        "provider":         "anthropic",
        "model":            "claude-3-5-sonnet-latest",
        "expires_at":       expires,
        "usage_cap_tokens": 1_000_000,
    }))

    cred = fetch_broker_credential(
        config_path=cfg,
        http_client=client,
        signing_secret=SIGNING_SECRET,
    )

    assert isinstance(cred, BrokeredCredential)
    assert cred.provider == "anthropic"
    assert cred.env_var == "ANTHROPIC_API_KEY"
    assert cred.api_key == "wk_broker_xxx"
    assert cred.model == "claude-3-5-sonnet-latest"
    assert cred.is_expired is False
    assert cred.usage_cap_tokens == 1_000_000

    # URL: the issue endpoint, not the legacy llm-credentials one.
    assert client.last_url == "http://pro.local:8098/api/v1/agent/credentials/issue"

    # Headers: HMAC signature in X-Windy-Signature, no Bearer.
    assert client.last_headers is not None
    assert "Authorization" not in client.last_headers
    assert client.last_headers["Content-Type"] == "application/json"
    assert client.last_headers["X-Windy-Signature"].startswith("sha256=")

    # Replay-protection timestamp must be a valid unix second string
    # within a small window of "now" — catches accidental ms conversion
    # or a frozen clock in the client.
    assert "X-Windy-Timestamp" in client.last_headers
    ts_header = client.last_headers["X-Windy-Timestamp"]
    assert isinstance(ts_header, str)
    ts = int(ts_header)
    now = int(time.time())
    assert abs(now - ts) < 5, f"timestamp {ts} not within 5s of now {now}"

    # Body: the contract-pinned payload, and its signature must verify.
    assert client.last_content is not None
    body_bytes = client.last_content
    body = json.loads(body_bytes.decode("utf-8"))
    assert body == {
        "windy_identity_id": "wi_abc",
        "passport_number":   "ET26-ABC-DEF",
        "scope":             DEFAULT_SCOPE,
        "duration_seconds":  DEFAULT_DURATION_SECONDS,
    }
    expected_sig = sign_broker_request(body_bytes, SIGNING_SECRET)
    assert client.last_headers["X-Windy-Signature"] == expected_sig


def test_fetch_broker_credential_sends_timestamp_header(tmp_path: Path, monkeypatch) -> None:
    """Pin the X-Windy-Timestamp header contract.

    Pro enforces a 300s replay window; if this header disappears the
    broker will reject every request. Freezing time.time() lets us
    assert on the exact value emitted."""
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(200, {
        "broker_token": "wk_broker_ts",
        "provider":     "openai",
        "model":        "gpt-4o-mini",
    }))

    frozen = 1_900_000_000  # arbitrary, well past 2024
    monkeypatch.setattr(time, "time", lambda: frozen)

    fetch_broker_credential(
        config_path=cfg, http_client=client, signing_secret=SIGNING_SECRET,
    )

    assert client.last_headers is not None
    assert client.last_headers["X-Windy-Timestamp"] == str(frozen)
    # Sanity: the timestamp does NOT leak into the signed body — the
    # HMAC contract from fix #2 is still "signature over body bytes".
    assert b"timestamp" not in (client.last_content or b"")


def test_fetch_broker_credential_respects_override_identity(tmp_path: Path) -> None:
    """Explicit windy_identity_id / passport_number wins over the config file."""
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(200, {
        "broker_token": "wk_broker_xxx",
        "provider":     "openai",
        "model":        "gpt-4o-mini",
    }))
    fetch_broker_credential(
        config_path=cfg,
        http_client=client,
        windy_identity_id="wi_override",
        passport_number="ET26-XXX-XXX",
        signing_secret=SIGNING_SECRET,
    )
    assert client.last_content is not None
    body = json.loads(client.last_content.decode("utf-8"))
    assert body["windy_identity_id"] == "wi_override"
    assert body["passport_number"] == "ET26-XXX-XXX"


def test_fetch_broker_credential_no_signing_secret(tmp_path: Path, monkeypatch) -> None:
    """Without a signing secret the client must NOT fire the request."""
    monkeypatch.delenv("WINDY_BROKER_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("WINDY_PRO_SIGNING_SECRET", raising=False)
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(200, {}))  # would succeed if called
    assert fetch_broker_credential(config_path=cfg, http_client=client) is None
    assert client.last_url is None, "Request should be skipped when unsigned"


def test_fetch_broker_credential_picks_up_env_signing_secret(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("WINDY_BROKER_SIGNING_SECRET", SIGNING_SECRET)
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(200, {
        "broker_token": "wk_broker_env",
        "provider":     "openai",
        "model":        "gpt-4o-mini",
    }))
    cred = fetch_broker_credential(config_path=cfg, http_client=client)
    assert cred is not None
    assert cred.api_key == "wk_broker_env"
    assert client.last_headers is not None
    assert client.last_headers["X-Windy-Signature"].startswith("sha256=")


def test_fetch_broker_credential_no_config(tmp_path: Path) -> None:
    assert fetch_broker_credential(
        config_path=tmp_path / "missing.json",
        signing_secret=SIGNING_SECRET,
    ) is None


def test_fetch_broker_credential_pro_5xx(tmp_path: Path) -> None:
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(503))
    assert fetch_broker_credential(
        config_path=cfg, http_client=client, signing_secret=SIGNING_SECRET,
    ) is None


def test_fetch_broker_credential_unknown_provider(tmp_path: Path) -> None:
    cfg = _valid_pro_cfg(tmp_path)
    client = _FakeClient(_FakeResponse(200, {
        "provider":     "fictional_llm",
        "broker_token": "x",
        "model":        "m",
    }))
    assert fetch_broker_credential(
        config_path=cfg, http_client=client, signing_secret=SIGNING_SECRET,
    ) is None


def test_fetch_broker_credential_missing_broker_token(tmp_path: Path) -> None:
    cfg = _valid_pro_cfg(tmp_path)
    # Response uses the old 'api_key' field name — must be rejected as
    # malformed now that the contract is 'broker_token'.
    client = _FakeClient(_FakeResponse(200, {
        "provider": "openai", "api_key": "oops-wrong-field",
    }))
    assert fetch_broker_credential(
        config_path=cfg, http_client=client, signing_secret=SIGNING_SECRET,
    ) is None


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
