"""Tests for the hatch orchestrator."""

from __future__ import annotations

import os
import tempfile

import pytest

from windyfly.hatch_orchestrator import HatchResult, orchestrate_hatch
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure no real service credentials leak into tests."""
    monkeypatch.delenv("ETERNITAS_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.delenv("WINDY_IDENTITY_ID", raising=False)
    monkeypatch.delenv("SYNAPSE_REGISTRATION_SECRET", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)
    monkeypatch.delenv("WINDYMAIL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_PHONE", raising=False)


class TestOrchestration:
    async def test_full_hatch_with_mocks(self, db):
        """Full hatch flow should complete with all mock services."""
        result = await orchestrate_hatch(
            agent_name="test-fly",
            owner_id="owner-1",
            owner_name="Grant",
            db=db,
        )

        assert isinstance(result, HatchResult)
        assert result.agent_name == "test-fly"
        assert result.owner_name == "Grant"

        # Eternitas should succeed (mock)
        assert result.passport_id.startswith("ET-L")
        assert result.passport_status == "active"

        # Mail should succeed (mock)
        assert result.mail_provisioned is True
        assert result.email_address.endswith("@windymail.ai")

        # Phone should succeed (mock)
        assert result.phone_provisioned is True
        assert result.phone_number.startswith("+1555")
        assert result.phone_is_mock is True

        # Birth certificate should be generated
        assert result.neural_fingerprint != ""
        assert result.certificate_number.startswith("WF-")

    async def test_hatch_generates_pdf(self, db):
        """Hatch should save a PDF birth certificate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}
            result = await orchestrate_hatch(
                agent_name="pdf-fly",
                db=db,
                config=config,
            )
            assert result.birth_certificate_path.endswith(".pdf")
            assert os.path.exists(result.birth_certificate_path)

    async def test_hatch_idempotent_passport(self, db):
        """Hatching the same agent twice should reuse the passport."""
        r1 = await orchestrate_hatch("same-fly", db=db)
        r2 = await orchestrate_hatch("same-fly", db=db)
        assert r1.passport_id == r2.passport_id

    async def test_hatch_sms_with_owner_phone(self, db, monkeypatch):
        """If OWNER_PHONE is set, hatch SMS should be sent (mock)."""
        monkeypatch.setenv("OWNER_PHONE", "+15559999999")
        result = await orchestrate_hatch("sms-fly", db=db)
        assert result.hatch_sms_sent is True

    async def test_hatch_without_owner_phone(self, db):
        """Without OWNER_PHONE, SMS step should be silently skipped."""
        result = await orchestrate_hatch("no-sms-fly", db=db)
        assert result.hatch_sms_sent is False
        # Should not be an error
        assert not any("SMS" in e for e in result.errors)

    async def test_matrix_skipped_without_secret(self, db):
        """Matrix should skip gracefully without Synapse secret."""
        result = await orchestrate_hatch("matrix-fly", db=db)
        assert result.matrix_provisioned is False
        # Non-fatal — logged as error but hatch continues
        assert any("Matrix" in e for e in result.errors)


class TestHatchResult:
    def test_defaults(self):
        r = HatchResult()
        assert r.agent_name == ""
        assert r.errors == []
        assert r.passport_id == ""
        assert r.matrix_provisioned is False

    def test_error_collection(self):
        r = HatchResult()
        r.errors.append("Test error")
        assert len(r.errors) == 1
