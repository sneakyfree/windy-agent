"""`windy go` must not claim services that do not exist (clean-machine journey, 2026-09-23)."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

import windyfly.hatching as hatching
from windyfly.quickstart import hatch_service_lines


def _result(**kw):
    base = dict(passport_id="", mail_provisioned=False, mail_is_mock=False, email_address="",
                phone_provisioned=False, phone_is_mock=False, phone_number="",
                matrix_user_id="", certificate_number="", birth_certificate_path="", errors=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_placeholder_mail_and_mock_phone_get_no_checkmark():
    lines = "\n".join(hatch_service_lines(_result(
        mail_provisioned=True, mail_is_mock=True, email_address="windy-fly@windymail.ai",
        phone_provisioned=True, phone_is_mock=True, phone_number="+15550001000")))
    assert "✓" not in lines
    assert "windy-fly@windymail.ai" not in lines and "+15550001000" not in lines
    assert "not set up" in lines


def test_real_services_keep_their_checkmark():
    lines = hatch_service_lines(_result(
        mail_provisioned=True, email_address="fly-1a2b3c@windymail.ai",
        phone_provisioned=True, phone_number="+18015550123"))
    assert lines[0].count("✓") == 1 and "fly-1a2b3c@windymail.ai" in lines[0]
    assert "✓" in lines[1] and "+18015550123" in lines[1]


def _render_table(monkeypatch, result, config=None):
    buf = io.StringIO()
    monkeypatch.setattr(hatching, "console", Console(file=buf, width=160, color_system=None))
    hatching.show_ecosystem_status(result, config)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ETERNITAS_URL", "ETERNITAS_API_URL", "WINDYFLY_ALLOW_FAKE_IDENTITY",
              "WINDYMAIL_EMAIL", "TWILIO_PHONE_NUMBER", "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(k, raising=False)


def test_real_passport_is_not_labelled_local(monkeypatch):
    out = _render_table(monkeypatch, _result(passport_id="ET26-VNC7-16G5"))
    assert "ET26-VNC7-16G5" in out and "local" not in out.split("ET26-VNC7-16G5")[1].split("\n")[0]


def test_mock_issuer_passport_is_labelled(monkeypatch):
    monkeypatch.setenv("ETERNITAS_URL", "mock://local")
    out = _render_table(monkeypatch, _result(passport_id="ET26-MOCK-0001"))
    assert "local mock" in out


def test_table_placeholder_mail_and_mock_phone_are_pending(monkeypatch):
    out = _render_table(monkeypatch, _result(
        mail_provisioned=True, mail_is_mock=True, email_address="windy-fly@windymail.ai",
        phone_provisioned=True, phone_is_mock=True, phone_number="+15550001000"))
    assert "windy-fly@windymail.ai" not in out and "+15550001000" not in out
    assert "Not set up" in out
