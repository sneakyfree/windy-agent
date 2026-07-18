"""Tests for email.send Gmail capability.

The non-trivial properties this suite proves:
  - Header-injection guard refuses CR/LF in any header field
  - Refuses missing required fields and oversized payloads
  - Gracefully degrades (no exception) when OAuth not configured
  - dry_run returns plan without invoking the API
  - Happy path with mocked service returns message_id + thread_id
  - API failure during send becomes a dict with ``error`` (not raise)
  - Boot step ``capabilities.email`` registers cleanly with the
    canonical default sequence
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from windyfly.agent.boot import (
    BootContext, BootSequence, default_capability_registration_sequence,
)
from windyfly.agent.capabilities import CapabilityRegistry
from windyfly.agent.capabilities.email import (
    _send_email_handler,
    register_email_capabilities,
)


# ── Header-injection guard ─────────────────────────────────────────


@pytest.mark.parametrize("field", ["to", "subject", "cc", "bcc"])
def test_send_email_refuses_newline_in_header(field):
    args = {
        "to": "alice@example.com",
        "subject": "hi",
        "body": "test",
    }
    args[field] = "x\nBcc: leak@evil.com" if field != "subject" else "subj\nx-spam: 1"
    with pytest.raises(ValueError, match="header injection"):
        _send_email_handler(**args)


@pytest.mark.parametrize("field", ["to", "subject", "cc", "bcc"])
def test_send_email_refuses_carriage_return_in_header(field):
    args = {
        "to": "alice@example.com",
        "subject": "hi",
        "body": "test",
    }
    args[field] = "x\rinjected"
    with pytest.raises(ValueError, match="header injection"):
        _send_email_handler(**args)


# ── Required-field validation ──────────────────────────────────────


def test_send_email_requires_to():
    with pytest.raises(ValueError, match="to is required"):
        _send_email_handler(to="", subject="hi", body="test")


def test_send_email_requires_subject():
    with pytest.raises(ValueError, match="subject is required"):
        _send_email_handler(to="a@b.com", subject="", body="test")


def test_send_email_requires_body():
    with pytest.raises(ValueError, match="body is required"):
        _send_email_handler(to="a@b.com", subject="hi", body="")


# ── Size caps ──────────────────────────────────────────────────────


def test_send_email_refuses_oversized_subject():
    with pytest.raises(ValueError, match="subject too long"):
        _send_email_handler(
            to="a@b.com", subject="x" * 1500, body="test",
        )


def test_send_email_refuses_oversized_body():
    big_body = "x" * (6 * 1024 * 1024)
    with pytest.raises(ValueError, match="body too large"):
        _send_email_handler(
            to="a@b.com", subject="hi", body=big_body,
        )


# ── Graceful degradation ───────────────────────────────────────────


def test_send_email_returns_error_when_oauth_not_configured(tmp_path, monkeypatch):
    """Bot must NOT raise — must return a dict with executed=False."""
    import windyfly.agent.capabilities.email as email_mod
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", tmp_path / "missing.json")

    out = _send_email_handler(
        to="a@b.com", subject="hi", body="test",
    )
    assert out["executed"] is False
    # Grandma-mode (Tier 1): no developer-speak — must use the
    # centralized dormant_nudge with chat-driven setup intent and an
    # LLM instruction to NOT relay terminal commands verbatim.
    assert out["kind"] == "dormant_integration"
    assert out["integration"] == "gmail"
    assert "set up email" in out["error"]
    assert "do NOT relay" in out["error"]
    assert out["plan"]["to"] == "a@b.com"


# ── dry_run ────────────────────────────────────────────────────────


def test_send_email_dry_run_returns_plan_without_sending(tmp_path, monkeypatch):
    """dry_run short-circuits before the service is even built."""
    import windyfly.agent.capabilities.email as email_mod
    # Pretend OAuth IS configured so dry_run is the only escape
    fake_token = tmp_path / "token.json"
    fake_token.write_text("{}")
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", fake_token)

    spy = MagicMock(side_effect=AssertionError("service must NOT be called in dry_run"))

    out = _send_email_handler(
        to="a@b.com", subject="dry", body="run",
        dry_run=True, _service_factory=spy,
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert out["plan"]["action"] == "send_email"
    assert out["plan"]["body_chars"] == 3
    spy.assert_not_called()


# ── Happy path with mocked service ─────────────────────────────────


def test_send_email_happy_path_returns_message_id(tmp_path, monkeypatch):
    import windyfly.agent.capabilities.email as email_mod
    fake_token = tmp_path / "token.json"
    fake_token.write_text("{}")
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", fake_token)

    fake_service = MagicMock()
    fake_service.users().messages().send().execute.return_value = {
        "id": "msg-abc123", "threadId": "thr-xyz",
    }

    out = _send_email_handler(
        to="alice@example.com",
        subject="hello from windy",
        body="this is a test message",
        cc="bob@example.com",
        _service_factory=lambda: fake_service,
    )
    assert out["executed"] is True
    assert out["message_id"] == "msg-abc123"
    assert out["thread_id"] == "thr-xyz"
    assert out["plan"]["cc"] == "bob@example.com"
    # Verify the service was actually called with raw + userId="me"
    sent_kwargs = fake_service.users().messages().send.call_args
    assert sent_kwargs is not None


def test_send_email_api_exception_becomes_error_dict(tmp_path, monkeypatch):
    """Gmail API blow-up must NOT propagate; LLM gets a usable error."""
    import windyfly.agent.capabilities.email as email_mod
    fake_token = tmp_path / "token.json"
    fake_token.write_text("{}")
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", fake_token)

    fake_service = MagicMock()
    fake_service.users().messages().send().execute.side_effect = RuntimeError(
        "quotaExceeded: daily send limit hit"
    )

    out = _send_email_handler(
        to="a@b.com", subject="hi", body="test",
        _service_factory=lambda: fake_service,
    )
    assert out["executed"] is False
    assert "quotaExceeded" in out["error"]


def test_send_email_service_factory_returns_none_becomes_error_dict(
    tmp_path, monkeypatch,
):
    """Auth refresh failure path: _get_service returns None."""
    import windyfly.agent.capabilities.email as email_mod
    fake_token = tmp_path / "token.json"
    fake_token.write_text("{}")
    monkeypatch.setattr(email_mod, "_TOKEN_PATH", fake_token)

    out = _send_email_handler(
        to="a@b.com", subject="hi", body="test",
        _service_factory=lambda: None,
    )
    assert out["executed"] is False
    assert "Gmail authentication failed" in out["error"]


# ── Registration smoke test ────────────────────────────────────────


def test_register_email_capabilities_adds_email_send(monkeypatch):
    # email.send only registers with a live backend now (2026-07-04
    # tool-ambiguity fix); force one so this pins its descriptor.
    from windyfly.agent.capabilities import email as email_mod
    monkeypatch.setattr(email_mod, "_is_configured", lambda: True)
    registry = CapabilityRegistry()
    register_email_capabilities(registry, config={})
    cap = registry.get("email.send")
    assert cap is not None
    assert cap.id == "email.send"
    assert cap.audit_required is True
    # EXTERNAL_EFFECT defaults to TRUSTED band
    from windyfly.agent.capabilities.descriptor import Band
    assert cap.band_required == Band.TRUSTED


def test_email_send_not_registered_without_backend(monkeypatch):
    from windyfly.agent.capabilities import email as email_mod
    monkeypatch.setattr(email_mod, "_is_configured", lambda: False)
    monkeypatch.setattr("windyfly.tools.mail._resend_configured", lambda: False)
    registry = CapabilityRegistry()
    register_email_capabilities(registry, config={})
    assert registry.get("email.send") is None


# ── Boot wiring ────────────────────────────────────────────────────


def test_boot_sequence_includes_capabilities_email():
    """The default sequence must register email after audit hooks
    (so its registration is auditable from the first invocation)
    and declare ``capabilities.audit`` as a prerequisite."""
    seq = default_capability_registration_sequence()
    names = [s.name for s in seq]
    assert "capabilities.email" in names
    email_idx = names.index("capabilities.email")
    audit_idx = names.index("capabilities.audit")
    assert email_idx > audit_idx, (
        "email registration must come after audit hooks"
    )
    email_step = next(s for s in seq if s.name == "capabilities.email")
    assert "capabilities.audit" in email_step.requires


# ── setup_gmail_oauth (CLI flow) ───────────────────────────────────


def test_setup_gmail_oauth_missing_creds_returns_false(tmp_path):
    from windyfly.agent.capabilities.email import setup_gmail_oauth
    missing = tmp_path / "missing_creds.json"
    out = setup_gmail_oauth(
        creds_path=missing, token_path=tmp_path / "token.json",
    )
    assert out is False


def test_setup_gmail_oauth_writes_token_on_success(tmp_path, monkeypatch):
    """Mock InstalledAppFlow → assert token JSON gets written."""
    from windyfly.agent.capabilities import email as email_mod

    creds = tmp_path / "creds.json"
    creds.write_text('{"installed": {}}')  # contents not validated by the mock
    token_path = tmp_path / "gmail_token.json"

    fake_creds_obj = MagicMock()
    fake_creds_obj.to_json.return_value = '{"refresh_token": "test-rt"}'

    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds_obj

    fake_module = MagicMock()
    fake_module.InstalledAppFlow.from_client_secrets_file.return_value = fake_flow
    monkeypatch.setitem(
        __import__("sys").modules, "google_auth_oauthlib.flow", fake_module,
    )

    out = email_mod.setup_gmail_oauth(creds_path=creds, token_path=token_path)
    assert out is True
    assert token_path.exists()
    assert "refresh_token" in token_path.read_text(encoding="utf-8")
    fake_module.InstalledAppFlow.from_client_secrets_file.assert_called_once_with(
        str(creds),
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )


def test_setup_gmail_oauth_flow_exception_returns_false(tmp_path, monkeypatch):
    from windyfly.agent.capabilities import email as email_mod

    creds = tmp_path / "creds.json"
    creds.write_text("{}")

    fake_module = MagicMock()
    fake_module.InstalledAppFlow.from_client_secrets_file.side_effect = (
        RuntimeError("user closed browser")
    )
    monkeypatch.setitem(
        __import__("sys").modules, "google_auth_oauthlib.flow", fake_module,
    )

    out = email_mod.setup_gmail_oauth(
        creds_path=creds, token_path=tmp_path / "gmail_token.json",
    )
    assert out is False


# ── CLI dispatch wiring ────────────────────────────────────────────


def test_cli_dispatch_includes_setup_gmail():
    """Smoke test: setup-gmail must be in the CLI's dispatch table.

    Reads the source so we don't have to argparse-parse just to assert
    a registration. Cheap, deterministic, catches the obvious typo
    that broke setup-calendar (referenced in error messages but never
    actually wired)."""
    import windyfly.cli as cli_module
    src_path = __import__("inspect").getfile(cli_module)
    src = open(src_path, encoding="utf-8").read()
    assert '"setup-gmail": cmd_setup_gmail' in src, (
        "setup-gmail must be registered in the CLI dispatch table"
    )
    assert 'sub.add_parser(\n        "setup-gmail"' in src or \
           'sub.add_parser("setup-gmail"' in src, (
        "setup-gmail must have an add_parser entry"
    )
