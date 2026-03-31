"""End-to-end hatch orchestration test with fully mocked external services.

Validates the complete 'Born Into' experience:
  1. Eternitas API → passport ET-99999
  2. Windy Mail API → agent@windymail.ai
  3. Matrix homeserver → @agent:chat.windypro.com
  4. Twilio → +1234567890
  5. orchestrate_hatch() produces a fully populated HatchResult
  6. Birth certificate PDF is generated
  7. Environment variables are written
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.hatch_orchestrator import HatchResult, orchestrate_hatch
from windyfly.memory.database import Database


@pytest.fixture
def db():
    """In-memory database for testing."""
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure no real credentials bleed into tests."""
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.delenv("SYNAPSE_REGISTRATION_SECRET", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)
    monkeypatch.delenv("WINDYMAIL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_PHONE", raising=False)
    monkeypatch.delenv("MATRIX_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MATRIX_BOT_PASSWORD", raising=False)


# ── Mock objects ────────────────────────────────────────────────────


class MockPassport:
    passport_id = "ET-99999"
    status = "active"


class MockEternitasClient:
    async def register(self, req):
        return MockPassport()


class MockMailServer:
    def __init__(self, db):
        pass

    async def provision_inbox(self, agent_name, passport_id):
        return {"email": "agent@windymail.ai", "status": "active"}


class MockMatrixResult:
    success = True
    user_id = "@agent:chat.windypro.com"
    homeserver = "https://chat.windypro.com"
    error = None


class MockPhoneResult:
    success = True
    phone_number = "+1234567890"
    is_mock = False
    error = None


# ── The E2E test ────────────────────────────────────────────────────


class TestHatchEndToEnd:
    """Full end-to-end hatch orchestration with all services mocked."""

    async def test_full_hatch_all_services_mocked(self, db, monkeypatch):
        """Mocks all four external services and verifies the complete flow."""
        monkeypatch.setenv("OWNER_PHONE", "+15551234567")

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            # Mock Eternitas
            with patch(
                "windyfly.hatch_orchestrator._step_eternitas",
                new_callable=AsyncMock,
            ) as mock_eternitas, \
                patch(
                "windyfly.hatch_orchestrator._step_matrix",
                new_callable=AsyncMock,
            ) as mock_matrix, \
                patch(
                "windyfly.hatch_orchestrator._step_mail",
                new_callable=AsyncMock,
            ) as mock_mail, \
                patch(
                "windyfly.hatch_orchestrator._step_phone",
                new_callable=AsyncMock,
            ) as mock_phone, \
                patch(
                "windyfly.hatch_orchestrator._step_hatch_sms",
                new_callable=AsyncMock,
            ) as mock_sms:
                # Configure mocks to populate HatchResult fields
                async def fill_eternitas(result, *args, **kwargs):
                    result.passport_id = "ET-99999"
                    result.passport_status = "active"
                    os.environ["ETERNITAS_PASSPORT"] = "ET-99999"

                async def fill_matrix(result, *args, **kwargs):
                    result.matrix_user_id = "@agent:chat.windypro.com"
                    result.matrix_homeserver = "https://chat.windypro.com"
                    result.matrix_provisioned = True

                async def fill_mail(result, *args, **kwargs):
                    result.email_address = "agent@windymail.ai"
                    result.mail_provisioned = True

                async def fill_phone(result, *args, **kwargs):
                    result.phone_number = "+1234567890"
                    result.phone_provisioned = True
                    result.phone_is_mock = False

                async def fill_sms(result, *args, **kwargs):
                    result.hatch_sms_sent = True

                mock_eternitas.side_effect = fill_eternitas
                mock_matrix.side_effect = fill_matrix
                mock_mail.side_effect = fill_mail
                mock_phone.side_effect = fill_phone
                mock_sms.side_effect = fill_sms

                result = await orchestrate_hatch(
                    agent_name="e2e-fly",
                    owner_id="owner-e2e",
                    owner_name="Grant",
                    config=config,
                    db=db,
                )

            # ── Assertions ──────────────────────────────────────────

            # All fields populated
            assert result.agent_name == "e2e-fly"
            assert result.owner_name == "Grant"

            # Eternitas
            assert result.passport_id == "ET-99999"
            assert result.passport_status == "active"

            # Matrix
            assert result.matrix_user_id == "@agent:chat.windypro.com"
            assert result.matrix_homeserver == "https://chat.windypro.com"
            assert result.matrix_provisioned is True

            # Mail
            assert result.email_address == "agent@windymail.ai"
            assert result.mail_provisioned is True

            # Phone
            assert result.phone_number == "+1234567890"
            assert result.phone_provisioned is True
            assert result.phone_is_mock is False

            # SMS
            assert result.hatch_sms_sent is True

            # Birth certificate — generated by the real _step_birth_certificate
            assert result.birth_certificate_path.endswith(".pdf")
            assert Path(result.birth_certificate_path).exists()
            assert result.certificate_number.startswith("WF-")
            assert result.neural_fingerprint != ""

            # No errors
            assert result.errors == [], f"Unexpected errors: {result.errors}"

    async def test_hatch_with_real_mock_services(self, db):
        """Test using the project's built-in mock services (no patches)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            result = await orchestrate_hatch(
                agent_name="real-mock-fly",
                owner_id="owner-real",
                owner_name="Grant",
                config=config,
                db=db,
            )

            # HatchResult is fully populated
            assert isinstance(result, HatchResult)
            assert result.agent_name == "real-mock-fly"

            # Eternitas — local mock gives ET-L* passport
            assert result.passport_id.startswith("ET-L")
            assert result.passport_status == "active"

            # Mail — mock mail server provisions
            assert result.mail_provisioned is True
            assert result.email_address.endswith("@windymail.ai")

            # Phone — mock phone
            assert result.phone_provisioned is True
            assert result.phone_number.startswith("+1555")

            # Birth certificate
            assert result.birth_certificate_path.endswith(".pdf")
            assert Path(result.birth_certificate_path).exists()

    async def test_env_vars_written_after_hatch(self, db, monkeypatch):
        """Verify that ETERNITAS_PASSPORT env var is set after hatch."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

        result = await orchestrate_hatch(
            agent_name="env-fly",
            db=db,
        )

        # Eternitas step writes ETERNITAS_PASSPORT to env
        assert os.environ.get("ETERNITAS_PASSPORT") == result.passport_id

    async def test_env_file_generation(self, db, monkeypatch):
        """Verify .env file can be generated from hatch results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}
            env_path = Path(tmpdir) / ".env"

            result = await orchestrate_hatch(
                agent_name="envfile-fly",
                owner_id="owner-env",
                owner_name="Grant",
                config=config,
                db=db,
            )

            # Write .env from result (simulating what the setup wizard does)
            env_lines = [
                f"ETERNITAS_PASSPORT={result.passport_id}",
                f"AGENT_EMAIL={result.email_address}",
                f"AGENT_PHONE={result.phone_number}",
                f"MATRIX_USER_ID={result.matrix_user_id}",
            ]
            env_path.write_text("\n".join(env_lines) + "\n")

            assert env_path.exists()
            content = env_path.read_text()
            assert result.passport_id in content
            assert result.email_address in content

    async def test_hatch_error_resilience(self, db):
        """Even when services fail, hatch should complete without raising."""
        with patch(
            "windyfly.hatch_orchestrator._step_eternitas",
            new_callable=AsyncMock,
        ) as mock_et:
            async def fail_eternitas(result, *args, **kwargs):
                result.errors.append("Eternitas: connection refused")

            mock_et.side_effect = fail_eternitas

            # Should not raise even when Eternitas fails
            result = await orchestrate_hatch(
                agent_name="resilient-fly",
                db=db,
            )

            assert isinstance(result, HatchResult)
            assert result.agent_name == "resilient-fly"
            assert "Eternitas: connection refused" in result.errors
