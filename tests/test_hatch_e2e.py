"""End-to-end tests for the full hatch flow.

Covers:
  1. orchestrate_hatch() — 7 steps in order, errors captured, HatchResult populated
  2. Naming ceremony in quickstart — prompts, env vars, config flow
  3. Birth certificate with hardware specs — PDF, fingerprint, "Creator" label
  4. retry_failed_provisioning() — partial failure, recovery file, retry, cleanup
  5. Hatch email with PDF attachment and SMS with agent name
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from windyfly.hatch_orchestrator import (
    HatchResult,
    _save_recovery,
    orchestrate_hatch,
    retry_failed_provisioning,
)
from windyfly.memory.database import Database


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip real credentials so tests use mocks."""
    for var in [
        "ETERNITAS_URL", "ETERNITAS_API_URL", "ETERNITAS_PASSPORT",
        "SYNAPSE_REGISTRATION_SECRET",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_JMAP_TOKEN", "WINDYMAIL_API_URL",
        "OWNER_PHONE", "OWNER_EMAIL",
        "WINDY_OWNER_NAME", "WINDY_OWNER_ID",
        "WINDYFLY_AGENT_NAME", "_WINDYFLY_HATCHING_PLAYED",
        "MATRIX_BOT_TOKEN", "MATRIX_BOT_PASSWORD",
        "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
        "SIGNAL_PHONE_NUMBER", "TEAMS_APP_ID", "IRC_SERVER",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Redirect recovery file to a temp directory."""
    recovery_path = tmp_path / "provision_recovery.json"
    monkeypatch.setattr(
        "windyfly.hatch_orchestrator._RECOVERY_PATH", recovery_path
    )
    return recovery_path


# ═══════════════════════════════════════════════════════════════════════
# 1. Full orchestrate_hatch() flow
# ═══════════════════════════════════════════════════════════════════════


class TestOrchestrateHatchFlow:
    """Verify all 7 steps execute, errors are non-blocking, result is populated."""

    async def test_all_steps_execute_with_mock_db(self, db, recovery_dir):
        """Full hatch with mock DB completes all provisioning steps."""
        result = await orchestrate_hatch(
            agent_name="e2e-fly",
            owner_id="owner-e2e",
            owner_name="TestOwner",
            db=db,
        )

        assert isinstance(result, HatchResult)
        assert result.agent_name == "e2e-fly"
        assert result.owner_name == "TestOwner"

        # Step 1: Eternitas
        assert result.passport_id.startswith("ET-")
        assert result.passport_status == "active"

        # Step 3: Mail (mock)
        assert result.mail_provisioned is True
        assert "@windymail.ai" in result.email_address

        # Step 4: Phone (mock)
        assert result.phone_provisioned is True
        assert result.phone_number.startswith("+1")
        assert result.phone_is_mock is True

        # Step 5: Birth certificate
        assert result.certificate_number.startswith("WF-")
        assert len(result.neural_fingerprint) == 64

        # Hardware specs collected
        assert isinstance(result.hardware_specs, dict)
        assert "cpu" in result.hardware_specs

    async def test_errors_captured_not_blocking(self, db, recovery_dir):
        """Matrix always fails without secret — captured but doesn't crash."""
        result = await orchestrate_hatch("error-fly", db=db)

        assert result.agent_name == "error-fly"
        assert result.matrix_provisioned is False
        assert any("Matrix" in e for e in result.errors)
        # Other steps still succeeded
        assert result.passport_id != ""
        assert result.mail_provisioned is True

    async def test_hatch_result_fields_independent(self):
        """Each HatchResult has its own error list (no shared mutable default)."""
        r1 = HatchResult()
        r2 = HatchResult()
        r1.errors.append("test")
        assert len(r2.errors) == 0

    async def test_model_id_from_env(self, db, monkeypatch, recovery_dir):
        """model_id is set from DEFAULT_MODEL env var."""
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-20250514")
        result = await orchestrate_hatch("model-fly", db=db)
        assert result.model_id == "claude-sonnet-4-20250514"

    async def test_concurrent_steps_all_complete(self, db, recovery_dir):
        """Steps 2/3/4 run concurrently — all complete independently."""
        result = await orchestrate_hatch("concurrent-fly", db=db)

        assert result.mail_provisioned is True
        assert result.phone_provisioned is True
        assert result.passport_id != ""
        assert result.certificate_number != ""

    async def test_birth_cert_skipped_without_passport(self, db, recovery_dir):
        """If Eternitas fails, birth cert is skipped with error."""
        with patch("windyfly.hatch_orchestrator._step_eternitas") as mock_et:
            async def _noop(result, *a, **kw):
                result.errors.append("Eternitas: test failure")
            mock_et.side_effect = _noop

            result = await orchestrate_hatch("no-passport-fly", db=db)

        assert result.passport_id == ""
        assert result.certificate_number == ""
        assert any("Birth cert: skipped" in e for e in result.errors)

    async def test_pdf_saved_to_disk(self, db):
        """Birth certificate PDF is saved and path is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}
            result = await orchestrate_hatch("pdf-fly", db=db, config=config)
            assert result.birth_certificate_path.endswith(".pdf")
            assert os.path.exists(result.birth_certificate_path)
            with open(result.birth_certificate_path, "rb") as f:
                assert f.read(5) == b"%PDF-"

    async def test_idempotent_passport(self, db, recovery_dir):
        """Hatching the same agent twice reuses the passport."""
        r1 = await orchestrate_hatch("same-fly", db=db)
        r2 = await orchestrate_hatch("same-fly", db=db)
        assert r1.passport_id == r2.passport_id

    async def test_env_var_set_after_hatch(self, db, monkeypatch, recovery_dir):
        """ETERNITAS_PASSPORT env var is set after successful hatch."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        result = await orchestrate_hatch("env-fly", db=db)
        assert os.environ.get("ETERNITAS_PASSPORT") == result.passport_id


# ═══════════════════════════════════════════════════════════════════════
# 2. Naming ceremony flow (quickstart.py)
# ═══════════════════════════════════════════════════════════════════════


class TestNamingCeremony:
    """Verify naming prompts, env vars, and hatch call in quickstart."""

    @patch("windyfly.quickstart.Confirm")
    @patch("windyfly.quickstart.Prompt")
    @patch("windyfly.hatching.play_hatching")
    @patch("windyfly.hatch_orchestrator.run_hatch")
    @patch("windyfly.birth_certificate.render_birth_certificate_terminal", return_value="[cert]")
    def test_agent_name_flows_to_env_and_hatch(
        self, mock_render, mock_hatch, mock_play, mock_prompt, mock_confirm
    ):
        """Agent name from prompt flows to env var and run_hatch call."""
        from windyfly.quickstart import _try_hatch_provisioning

        mock_prompt.ask.side_effect = ["Buzzy", "skip", "skip", "skip"]
        mock_confirm.ask.return_value = True
        mock_hatch.return_value = HatchResult(
            agent_name="Buzzy",
            passport_id="ET-L-TEST",
            passport_status="active",
            certificate_number="WF-ABCD1234",
            neural_fingerprint="a" * 64,
            birth_certificate_path="/tmp/test.pdf",
        )

        _try_hatch_provisioning()

        assert os.environ.get("WINDYFLY_AGENT_NAME") == "Buzzy"
        mock_hatch.assert_called_once()
        call_kwargs = mock_hatch.call_args
        assert call_kwargs.kwargs.get("agent_name") == "Buzzy" or \
               call_kwargs[1].get("agent_name") == "Buzzy"

    @patch("windyfly.quickstart.Confirm")
    @patch("windyfly.quickstart.Prompt")
    @patch("windyfly.hatching.play_hatching")
    @patch("windyfly.hatch_orchestrator.run_hatch")
    @patch("windyfly.birth_certificate.render_birth_certificate_terminal", return_value="[cert]")
    def test_rename_on_deny(
        self, mock_render, mock_hatch, mock_play, mock_prompt, mock_confirm
    ):
        """If user denies first name, second prompt is used."""
        mock_prompt.ask.side_effect = ["BadName", "GoodName", "skip", "skip", "skip"]
        mock_confirm.ask.return_value = False
        mock_hatch.return_value = HatchResult(
            agent_name="GoodName",
            passport_id="ET-L-TEST",
            passport_status="active",
            certificate_number="WF-ABCD1234",
            neural_fingerprint="a" * 64,
        )

        from windyfly.quickstart import _try_hatch_provisioning
        _try_hatch_provisioning()

        assert os.environ.get("WINDYFLY_AGENT_NAME") == "GoodName"

    @patch("windyfly.quickstart.Confirm")
    @patch("windyfly.quickstart.Prompt")
    @patch("windyfly.hatching.play_hatching")
    @patch("windyfly.hatch_orchestrator.run_hatch")
    @patch("windyfly.birth_certificate.render_birth_certificate_terminal", return_value="[cert]")
    def test_owner_info_flows_to_env(
        self, mock_render, mock_hatch, mock_play, mock_prompt, mock_confirm
    ):
        """Creator name, phone, email flow into env vars."""
        mock_prompt.ask.side_effect = ["TestBot", "Grant", "+15551234567", "grant@test.com"]
        mock_confirm.ask.return_value = True
        mock_hatch.return_value = HatchResult(
            agent_name="TestBot",
            passport_id="ET-L-TEST",
            passport_status="active",
            certificate_number="WF-TEST1234",
            neural_fingerprint="b" * 64,
        )

        from windyfly.quickstart import _try_hatch_provisioning
        _try_hatch_provisioning()

        assert os.environ.get("WINDY_OWNER_NAME") == "Grant"
        assert os.environ.get("OWNER_PHONE") == "+15551234567"
        assert os.environ.get("OWNER_EMAIL") == "grant@test.com"

    @patch("windyfly.quickstart.Confirm")
    @patch("windyfly.quickstart.Prompt")
    @patch("windyfly.hatching.play_hatching")
    @patch("windyfly.hatch_orchestrator.run_hatch")
    def test_orchestrator_fallback(
        self, mock_hatch, mock_play, mock_prompt, mock_confirm
    ):
        """If orchestrator crashes, fallback provisions are attempted."""
        mock_prompt.ask.side_effect = ["FallbackFly", "skip", "skip", "skip"]
        mock_confirm.ask.return_value = True
        mock_hatch.side_effect = RuntimeError("Orchestrator exploded")

        with patch("windyfly.quickstart._try_matrix_provision") as mock_matrix, \
             patch("windyfly.quickstart._try_mail_provision") as mock_mail:
            from windyfly.quickstart import _try_hatch_provisioning
            _try_hatch_provisioning()
            mock_matrix.assert_called_once()
            mock_mail.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# 3. Birth certificate with hardware specs
# ═══════════════════════════════════════════════════════════════════════


class TestBirthCertificateE2E:
    """PDF creation, neural fingerprint determinism, hardware in output, Creator label."""

    def test_pdf_is_valid(self):
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            render_birth_certificate_pdf,
        )

        cert = generate_birth_certificate(
            agent_name="PdfFly",
            passport_id="ET-L-PDF123",
            owner_name="TestCreator",
            model_id="gpt-4o-mini",
            hardware_specs={"cpu": "Test CPU", "ram": "16 GB", "gpu": "Test GPU", "os": "TestOS"},
        )
        pdf_bytes = render_birth_certificate_pdf(cert)
        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 1000

    def test_fingerprint_is_deterministic(self):
        from windyfly.birth_certificate import generate_neural_fingerprint

        fp1 = generate_neural_fingerprint("hello", "world", "gpt-4o", "ET-123", "2025-01-01")
        fp2 = generate_neural_fingerprint("hello", "world", "gpt-4o", "ET-123", "2025-01-01")
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_different_inputs_different_fingerprints(self):
        from windyfly.birth_certificate import generate_neural_fingerprint

        fp1 = generate_neural_fingerprint("hello", "world", "gpt-4o", "ET-123", "2025-01-01")
        fp2 = generate_neural_fingerprint("hello", "world", "gpt-4o", "ET-456", "2025-01-01")
        assert fp1 != fp2

    def test_hardware_specs_in_terminal_output(self):
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            render_birth_certificate_terminal,
        )

        specs = {"cpu": "Apple M2 Ultra", "ram": "64 GB", "gpu": "Apple M2 Ultra", "os": "macOS 14.0"}
        cert = generate_birth_certificate(
            agent_name="HwFly",
            passport_id="ET-L-HW001",
            hardware_specs=specs,
        )
        terminal_text = render_birth_certificate_terminal(cert)
        assert "Apple M2 Ultra" in terminal_text
        assert "64 GB" in terminal_text

    def test_creator_label_not_owner(self):
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            render_birth_certificate_terminal,
        )

        cert = generate_birth_certificate(
            agent_name="LabelFly",
            passport_id="ET-L-LABEL01",
            owner_name="Grant Whitmer",
        )
        terminal_text = render_birth_certificate_terminal(cert)
        assert "Creator:" in terminal_text
        assert "Owner:" not in terminal_text

    def test_save_creates_file(self):
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            save_birth_certificate,
        )

        cert = generate_birth_certificate(
            agent_name="SaveFly",
            passport_id="ET-L-SAVE01",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_birth_certificate(cert, directory=tmpdir)
            assert os.path.exists(path)
            assert path.endswith(".pdf")
            assert "ET-L-SAVE01" in path

    def test_certificate_number_format(self):
        from windyfly.birth_certificate import generate_birth_certificate

        cert = generate_birth_certificate(
            agent_name="FmtFly",
            passport_id="ET-L-FMT001",
        )
        assert cert.certificate_number.startswith("WF-")
        assert len(cert.certificate_number) == 11  # WF- + 8 hex chars

    def test_hardware_specs_collected(self):
        from windyfly.birth_certificate import collect_hardware_specs

        specs = collect_hardware_specs()
        assert "cpu" in specs
        assert "ram" in specs
        assert "os" in specs
        assert specs["cpu"] != ""
        assert specs["os"] != ""


# ═══════════════════════════════════════════════════════════════════════
# 4. retry_failed_provisioning() recovery flow
# ═══════════════════════════════════════════════════════════════════════


class TestRetryRecoveryFlow:
    """Recovery file written on failure, retry picks up, cleanup on success."""

    async def test_recovery_file_written_on_failure(self, db, recovery_dir):
        """When a real step fails, recovery file is created."""
        with patch("windyfly.hatch_orchestrator._step_eternitas") as mock_et:
            async def _fail(result, *a, **kw):
                result.errors.append("Eternitas: connection refused")
            mock_et.side_effect = _fail

            await orchestrate_hatch("recovery-fly", db=db)

        assert recovery_dir.exists()
        data = json.loads(recovery_dir.read_text())
        assert "eternitas" in data["failed_steps"]
        assert data["agent_name"] == "recovery-fly"

    async def test_recovery_file_cleaned_on_success(self, db, recovery_dir):
        """When all steps succeed, no recovery file remains."""
        # Pre-create a recovery file
        recovery_dir.parent.mkdir(parents=True, exist_ok=True)
        recovery_dir.write_text(json.dumps({"failed_steps": ["eternitas"], "retry_count": 0}))

        await orchestrate_hatch("success-fly", db=db)

        # Matrix failure is expected (no Synapse secret) but filtered out
        if recovery_dir.exists():
            data = json.loads(recovery_dir.read_text())
            assert "eternitas" not in data.get("failed_steps", [])

    async def test_retry_picks_up_failed_steps(self, recovery_dir):
        """retry_failed_provisioning reads recovery file and retries."""
        recovery_dir.parent.mkdir(parents=True, exist_ok=True)
        recovery_dir.write_text(json.dumps({
            "failed_steps": ["eternitas"],
            "agent_name": "retry-fly",
            "passport_id": "",
            "retry_count": 0,
            "errors": ["Eternitas: connection refused"],
            "last_attempt": datetime.now(timezone.utc).isoformat(),
        }))

        with patch("windyfly.hatch_orchestrator._step_eternitas") as mock_et:
            async def _succeed(result, *a, **kw):
                result.passport_id = "ET-L-RECOVERED"
                result.passport_status = "active"
            mock_et.side_effect = _succeed

            result = await retry_failed_provisioning()

        assert result is not None
        assert result.passport_id == "ET-L-RECOVERED"
        assert not recovery_dir.exists()

    async def test_retry_increments_count(self, recovery_dir):
        """If retry fails again, retry_count increments."""
        recovery_dir.parent.mkdir(parents=True, exist_ok=True)
        recovery_dir.write_text(json.dumps({
            "failed_steps": ["matrix"],
            "agent_name": "retry-fly",
            "passport_id": "ET-L-123",
            "retry_count": 2,
            "errors": [],
            "last_attempt": datetime.now(timezone.utc).isoformat(),
        }))

        result = await retry_failed_provisioning()

        assert result is not None
        assert recovery_dir.exists()
        data = json.loads(recovery_dir.read_text())
        assert data["retry_count"] == 3

    async def test_retry_returns_none_when_no_recovery(self, recovery_dir):
        """No recovery file means nothing to retry."""
        assert not recovery_dir.exists()
        result = await retry_failed_provisioning()
        assert result is None

    async def test_retry_handles_corrupt_file(self, recovery_dir):
        """Corrupt recovery file is deleted gracefully."""
        recovery_dir.parent.mkdir(parents=True, exist_ok=True)
        recovery_dir.write_text("not json {{{")
        result = await retry_failed_provisioning()
        assert result is None
        assert not recovery_dir.exists()

    async def test_save_recovery_preserves_retry_count(self, recovery_dir):
        """_save_recovery reads existing retry_count from file."""
        recovery_dir.parent.mkdir(parents=True, exist_ok=True)
        recovery_dir.write_text(json.dumps({
            "retry_count": 5,
            "failed_steps": ["eternitas"],
        }))

        result = HatchResult(agent_name="preserve-fly")
        result.errors.append("Eternitas: still failing")
        _save_recovery(result)

        data = json.loads(recovery_dir.read_text())
        assert data["retry_count"] == 5


# ═══════════════════════════════════════════════════════════════════════
# 5. Hatch email (PDF attachment) and SMS (agent name)
# ═══════════════════════════════════════════════════════════════════════


class TestHatchEmailAndSMS:
    """Email has PDF attachment as base64, SMS contains agent name."""

    def test_sms_contains_agent_name(self):
        from windyfly.hatch_actions import format_hatch_sms

        msg = format_hatch_sms("BuzzyBot")
        assert "BuzzyBot" in msg
        assert "IT'S ALIVE" in msg
        assert "windyword" in msg.lower() or "windy" in msg.lower()

    async def test_sms_mock_fallback(self):
        """Without Twilio creds, SMS falls back to mock."""
        from windyfly.hatch_actions import send_hatch_sms

        result = await send_hatch_sms("+15551234567", "MockFly")
        assert result["status"] == "mock_sent"
        assert result["to"] == "+15551234567"
        assert "MockFly" in result["message"]

    def test_hatch_email_format(self):
        from windyfly.hatch_email import format_hatch_email

        email = format_hatch_email(
            agent_name="EmailFly",
            passport_id="ET-L-EMAIL01",
            agent_email="emailfly@windymail.ai",
            agent_phone="+15551234567",
            model_id="gpt-4o",
            certificate_number="WF-ABCD1234",
            neural_fingerprint="f" * 64,
        )

        assert "EmailFly" in email["subject"]
        assert "IT'S ALIVE" in email["subject"] or "It's Alive" in email["subject"]
        assert "EmailFly" in email["html"]
        assert "ET-L-EMAIL01" in email["html"]
        assert "emailfly@windymail.ai" in email["html"]
        assert "WF-ABCD1234" in email["text"]

    async def test_email_step_attaches_pdf(self, db, monkeypatch):
        """Step 7 reads the PDF and attaches as base64."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}
            result = await orchestrate_hatch("attach-fly", db=db, config=config)

            monkeypatch.setenv("OWNER_EMAIL", "test@example.com")
            monkeypatch.setenv("WINDYMAIL_API_URL", "http://localhost:9999")
            monkeypatch.setenv("WINDYMAIL_SERVICE_TOKEN", "test-token")

            captured_payload = {}

            async def _mock_post(self, url, **kwargs):
                captured_payload.update(kwargs.get("json", {}))
                return httpx.Response(200, json={"ok": True})

            with patch.object(httpx.AsyncClient, "post", _mock_post):
                from windyfly.hatch_orchestrator import _step_hatch_email
                await _step_hatch_email(result)

            assert result.hatch_email_sent is True
            assert "attachments" in captured_payload
            att = captured_payload["attachments"][0]
            assert att["content_type"] == "application/pdf"
            assert att["filename"].endswith(".pdf")
            pdf_bytes = base64.b64decode(att["content_base64"])
            assert pdf_bytes[:5] == b"%PDF-"

    async def test_email_step_skipped_without_env(self, db, recovery_dir):
        """Without OWNER_EMAIL, step 7 is silently skipped."""
        result = await orchestrate_hatch("no-email-fly", db=db)
        assert result.hatch_email_sent is False
        assert not any("Birth email" in e for e in result.errors)

    async def test_sms_step_with_owner_phone(self, db, monkeypatch, recovery_dir):
        """With OWNER_PHONE set, SMS is sent (mock)."""
        monkeypatch.setenv("OWNER_PHONE", "+15559876543")
        result = await orchestrate_hatch("sms-fly", db=db)
        assert result.hatch_sms_sent is True

    async def test_sms_step_skipped_without_phone(self, db, recovery_dir):
        """Without OWNER_PHONE, SMS step is silently skipped."""
        result = await orchestrate_hatch("no-sms-fly", db=db)
        assert result.hatch_sms_sent is False
        assert not any("SMS" in e for e in result.errors)

    def test_email_defaults_for_missing_fields(self):
        """Missing fields get sensible defaults in email."""
        from windyfly.hatch_email import format_hatch_email

        email = format_hatch_email(agent_name="DefaultFly")
        assert "Pending" in email["html"]
        assert "Not assigned" in email["html"]
        assert "DefaultFly" in email["text"]
