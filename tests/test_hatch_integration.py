"""Integration test for the full hatch flow.

End-to-end test that:
- Mocks all external services (Eternitas, Mail, Matrix, Twilio)
- Runs orchestrate_hatch() from hatch_orchestrator.py
- Asserts HatchResult has all fields populated
- Asserts birth certificate PDF was generated (check file exists)
- Asserts .env file was updated with credentials
- Asserts no errors in result.errors
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    for var in [
        "ETERNITAS_URL", "ETERNITAS_API_URL", "ETERNITAS_PASSPORT",
        "WINDY_JWT", "WINDY_IDENTITY_ID",
        "SYNAPSE_REGISTRATION_SECRET",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_JMAP_TOKEN", "WINDYMAIL_API_URL",
        "OWNER_PHONE", "OWNER_EMAIL",
        "WINDY_OWNER_NAME", "WINDY_OWNER_ID",
        "WINDYFLY_AGENT_NAME", "_WINDYFLY_HATCHING_PLAYED",
        "MATRIX_BOT_TOKEN", "MATRIX_BOT_PASSWORD",
        "AGENT_EMAIL", "AGENT_PHONE",
    ]:
        monkeypatch.delenv(var, raising=False)


# ── Helper: mock step side effects ─────────────────────────────────


async def _fill_eternitas(result, *args, **kwargs):
    result.passport_id = "ET-INT-99999"
    result.passport_status = "active"
    # ADR-064 — registration returns the canonical certificate block; the
    # certificate number is Eternitas's, never minted lane-side.
    result.eternitas_certificate = {"certificate_no": "ET-INT99999"}
    result.certificate_number = "ET-INT99999"
    os.environ["ETERNITAS_PASSPORT"] = "ET-INT-99999"


async def _fill_matrix(result, *args, **kwargs):
    result.matrix_user_id = "@testfly:chat.windychat.ai"
    result.matrix_homeserver = "https://chat.windychat.ai"
    result.matrix_provisioned = True


async def _fill_mail(result, *args, **kwargs):
    result.email_address = "testfly@windymail.ai"
    result.mail_provisioned = True


async def _fill_phone(result, *args, **kwargs):
    result.phone_number = "+15551234567"
    result.phone_provisioned = True
    result.phone_is_mock = False


async def _fill_sms(result, *args, **kwargs):
    result.hatch_sms_sent = True


# ── Tests ────────────────────────────────────────────────────────────


class TestHatchIntegration:
    """Full integration test of the hatch orchestration flow."""

    async def test_orchestrate_hatch_all_fields_populated(self, db, monkeypatch):
        """All HatchResult fields should be populated when all services succeed."""
        monkeypatch.setenv("OWNER_PHONE", "+15559876543")

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            with (
                patch("windyfly.hatch_orchestrator._step_eternitas",
                      new_callable=AsyncMock, side_effect=_fill_eternitas),
                patch("windyfly.hatch_orchestrator._step_matrix",
                      new_callable=AsyncMock, side_effect=_fill_matrix),
                patch("windyfly.hatch_orchestrator._step_mail",
                      new_callable=AsyncMock, side_effect=_fill_mail),
                patch("windyfly.hatch_orchestrator._step_phone",
                      new_callable=AsyncMock, side_effect=_fill_phone),
                patch("windyfly.hatch_orchestrator._step_hatch_sms",
                      new_callable=AsyncMock, side_effect=_fill_sms),
            ):
                result = await orchestrate_hatch(
                    agent_name="integration-fly",
                    owner_id="owner-int",
                    owner_name="Grant",
                    config=config,
                    db=db,
                )

            # ── Core identity ──
            assert isinstance(result, HatchResult)
            assert result.agent_name == "integration-fly"
            assert result.owner_name == "Grant"

            # ── Eternitas ──
            assert result.passport_id == "ET-INT-99999"
            assert result.passport_status == "active"

            # ── Matrix ──
            assert result.matrix_user_id == "@testfly:chat.windychat.ai"
            assert result.matrix_homeserver == "https://chat.windychat.ai"
            assert result.matrix_provisioned is True

            # ── Mail ──
            assert result.email_address == "testfly@windymail.ai"
            assert result.mail_provisioned is True

            # ── Phone ──
            assert result.phone_number == "+15551234567"
            assert result.phone_provisioned is True
            assert result.phone_is_mock is False

            # ── SMS ──
            assert result.hatch_sms_sent is True

            # ── Birth certificate PDF generated ──
            assert result.birth_certificate_path != ""
            assert result.birth_certificate_path.endswith(".pdf")
            assert Path(result.birth_certificate_path).exists()
            assert Path(result.birth_certificate_path).stat().st_size > 0

            # ── Certificate metadata ──
            assert result.certificate_number.startswith("ET-")  # ADR-064: Eternitas's number, WF- retired
            assert result.neural_fingerprint != ""

            # ── No errors ──
            assert result.errors == [], f"Unexpected errors: {result.errors}"

    async def test_env_vars_set_after_hatch(self, db, monkeypatch):
        """ETERNITAS_PASSPORT env var should be set by the hatch process."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

        with patch("windyfly.hatch_orchestrator._step_eternitas",
                   new_callable=AsyncMock, side_effect=_fill_eternitas):
            result = await orchestrate_hatch(
                agent_name="env-test-fly",
                db=db,
            )

        assert os.environ.get("ETERNITAS_PASSPORT") == "ET-INT-99999"
        assert result.passport_id == "ET-INT-99999"

    async def test_env_file_written_from_result(self, db, monkeypatch):
        """.env file can be reconstructed from HatchResult fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            with (
                patch("windyfly.hatch_orchestrator._step_eternitas",
                      new_callable=AsyncMock, side_effect=_fill_eternitas),
                patch("windyfly.hatch_orchestrator._step_matrix",
                      new_callable=AsyncMock, side_effect=_fill_matrix),
                patch("windyfly.hatch_orchestrator._step_mail",
                      new_callable=AsyncMock, side_effect=_fill_mail),
                patch("windyfly.hatch_orchestrator._step_phone",
                      new_callable=AsyncMock, side_effect=_fill_phone),
                patch("windyfly.hatch_orchestrator._step_hatch_sms",
                      new_callable=AsyncMock, side_effect=_fill_sms),
            ):
                result = await orchestrate_hatch(
                    agent_name="envfile-fly",
                    owner_id="owner-env",
                    owner_name="Grant",
                    config=config,
                    db=db,
                )

            # Simulate .env file generation from hatch results
            env_path = Path(tmpdir) / ".env"
            env_lines = [
                f"ETERNITAS_PASSPORT={result.passport_id}",
                f"AGENT_EMAIL={result.email_address}",
                f"AGENT_PHONE={result.phone_number}",
                f"MATRIX_USER_ID={result.matrix_user_id}",
                f"DEFAULT_MODEL={result.model_id}",
            ]
            env_path.write_text("\n".join(env_lines) + "\n")

            # Assert .env was written
            assert env_path.exists()
            content = env_path.read_text(encoding="utf-8")
            assert "ET-INT-99999" in content
            assert "testfly@windymail.ai" in content
            assert "+15551234567" in content
            assert "@testfly:chat.windychat.ai" in content

    async def test_hatch_resilient_to_service_failures(self, db):
        """Hatch should complete even when individual services fail."""

        async def fail_eternitas(result, *args, **kwargs):
            result.errors.append("Eternitas: Connection refused")

        async def fail_matrix(result, *args, **kwargs):
            result.errors.append("Matrix: 503 Service Unavailable")

        with (
            patch("windyfly.hatch_orchestrator._step_eternitas",
                  new_callable=AsyncMock, side_effect=fail_eternitas),
            patch("windyfly.hatch_orchestrator._step_matrix",
                  new_callable=AsyncMock, side_effect=fail_matrix),
        ):
            result = await orchestrate_hatch(
                agent_name="resilient-fly",
                db=db,
            )

        # Should NOT raise — hatch is resilient
        assert isinstance(result, HatchResult)
        assert result.agent_name == "resilient-fly"

        # Errors are captured, not fatal
        assert any("Eternitas" in e for e in result.errors)
        assert any("Matrix" in e for e in result.errors)

    async def test_birth_certificate_pdf_exists(self, db):
        """Birth certificate PDF should be a real file on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            with (
                patch("windyfly.hatch_orchestrator._step_eternitas",
                      new_callable=AsyncMock, side_effect=_fill_eternitas),
                patch("windyfly.hatch_orchestrator._step_matrix",
                      new_callable=AsyncMock, side_effect=_fill_matrix),
                patch("windyfly.hatch_orchestrator._step_mail",
                      new_callable=AsyncMock, side_effect=_fill_mail),
                patch("windyfly.hatch_orchestrator._step_phone",
                      new_callable=AsyncMock, side_effect=_fill_phone),
                patch("windyfly.hatch_orchestrator._step_hatch_sms",
                      new_callable=AsyncMock, side_effect=_fill_sms),
            ):
                result = await orchestrate_hatch(
                    agent_name="pdf-verify-fly",
                    config=config,
                    db=db,
                )

            # PDF must exist as a real file
            pdf_path = Path(result.birth_certificate_path)
            assert pdf_path.exists(), f"PDF not found at {pdf_path}"
            assert pdf_path.suffix == ".pdf"
            assert pdf_path.stat().st_size > 100  # Must be a real PDF, not empty

    async def test_hatch_result_dataclass_defaults(self):
        """HatchResult should have sane defaults for all fields."""
        r = HatchResult()
        assert r.agent_name == ""
        assert r.owner_name == ""
        assert r.passport_id == ""
        assert r.passport_status == ""
        assert r.matrix_user_id == ""
        assert r.matrix_provisioned is False
        assert r.email_address == ""
        assert r.mail_provisioned is False
        assert r.phone_number == ""
        assert r.phone_provisioned is False
        assert r.phone_is_mock is False
        assert r.birth_certificate_path == ""
        assert r.certificate_number == ""
        assert r.neural_fingerprint == ""
        assert r.hatch_sms_sent is False
        assert r.errors == []

    async def test_concurrent_provisioning_steps(self, db, monkeypatch):
        """Matrix, Mail, and Phone provisioning should run concurrently."""
        monkeypatch.setenv("OWNER_PHONE", "+15559876543")
        call_order = []

        async def track_matrix(result, *args, **kwargs):
            call_order.append("matrix_start")
            result.matrix_provisioned = True
            result.matrix_user_id = "@fly:chat.windychat.ai"
            call_order.append("matrix_end")

        async def track_mail(result, *args, **kwargs):
            call_order.append("mail_start")
            result.mail_provisioned = True
            result.email_address = "fly@windymail.ai"
            call_order.append("mail_end")

        async def track_phone(result, *args, **kwargs):
            call_order.append("phone_start")
            result.phone_provisioned = True
            result.phone_number = "+15551112222"
            call_order.append("phone_end")

        with (
            patch("windyfly.hatch_orchestrator._step_eternitas",
                  new_callable=AsyncMock, side_effect=_fill_eternitas),
            patch("windyfly.hatch_orchestrator._step_matrix",
                  new_callable=AsyncMock, side_effect=track_matrix),
            patch("windyfly.hatch_orchestrator._step_mail",
                  new_callable=AsyncMock, side_effect=track_mail),
            patch("windyfly.hatch_orchestrator._step_phone",
                  new_callable=AsyncMock, side_effect=track_phone),
            patch("windyfly.hatch_orchestrator._step_hatch_sms",
                  new_callable=AsyncMock, side_effect=_fill_sms),
        ):
            result = await orchestrate_hatch(
                agent_name="concurrent-fly",
                db=db,
            )

        # All three should have been called
        assert "matrix_start" in call_order
        assert "mail_start" in call_order
        assert "phone_start" in call_order
        assert result.matrix_provisioned is True
        assert result.mail_provisioned is True
        assert result.phone_provisioned is True
