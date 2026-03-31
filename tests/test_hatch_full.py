"""Full hatch orchestrator integration test.

Runs orchestrate_hatch() with mock services and verifies the complete
HatchResult contains valid data from every provisioning step.
"""

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
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.delenv("ETERNITAS_OPERATOR_KEY", raising=False)
    monkeypatch.delenv("SYNAPSE_REGISTRATION_SECRET", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)
    monkeypatch.delenv("WINDYMAIL_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_PHONE", raising=False)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)


class TestFullHatchIntegration:
    """End-to-end hatch with all mock services."""

    async def test_full_hatch_produces_valid_result(self, db):
        """Full hatch with mock services should produce a complete HatchResult."""
        result = await orchestrate_hatch(
            agent_name="contract-fly",
            owner_id="owner-1",
            owner_name="Grant",
            db=db,
        )

        assert isinstance(result, HatchResult)
        assert result.agent_name == "contract-fly"
        assert result.owner_name == "Grant"

        # Eternitas — passport issued via mock
        assert result.passport_id.startswith("ET-")
        assert result.passport_status == "active"

        # Mail — provisioned via mock
        assert result.mail_provisioned is True
        assert result.email_address.endswith("@windymail.ai")

        # Phone — provisioned via mock
        assert result.phone_provisioned is True
        assert result.phone_number.startswith("+1555")
        assert result.phone_is_mock is True

        # Birth certificate
        assert result.neural_fingerprint != ""
        assert result.certificate_number.startswith("WF-")

    async def test_hatch_passport_id_format(self, db):
        """Passport ID should match the Eternitas format (ET-LXXXXX for mock)."""
        result = await orchestrate_hatch("format-fly", db=db)

        assert result.passport_id.startswith("ET-L")
        num_part = result.passport_id.split("ET-L")[1]
        assert num_part.isdigit()
        assert len(num_part) == 5

    async def test_hatch_birth_certificate_exists_on_disk(self, db):
        """Birth certificate PDF should be written to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}
            result = await orchestrate_hatch(
                agent_name="pdf-fly",
                db=db,
                config=config,
            )
            assert result.birth_certificate_path.endswith(".pdf")
            assert os.path.exists(result.birth_certificate_path)
            # PDF should have non-trivial content
            size = os.path.getsize(result.birth_certificate_path)
            assert size > 100

    async def test_hatch_idempotent_passport(self, db):
        """Hatching the same agent twice should reuse the passport."""
        r1 = await orchestrate_hatch("idem-fly", db=db)
        r2 = await orchestrate_hatch("idem-fly", db=db)
        assert r1.passport_id == r2.passport_id

    async def test_hatch_different_agents_get_different_passports(self, db):
        """Different agents get different passports."""
        r1 = await orchestrate_hatch("fly-alpha", db=db)
        r2 = await orchestrate_hatch("fly-beta", db=db)
        assert r1.passport_id != r2.passport_id

    async def test_hatch_different_agents_get_different_emails(self, db):
        """Different agents get different email addresses."""
        r1 = await orchestrate_hatch("email-a", db=db)
        r2 = await orchestrate_hatch("email-b", db=db)
        assert r1.email_address != r2.email_address
        assert "email-a" in r1.email_address
        assert "email-b" in r2.email_address


class TestHatchSMS:
    async def test_sms_sent_with_owner_phone(self, db, monkeypatch):
        """If OWNER_PHONE is set, hatch SMS should be sent."""
        monkeypatch.setenv("OWNER_PHONE", "+15559999999")
        result = await orchestrate_hatch("sms-fly", db=db)
        assert result.hatch_sms_sent is True

    async def test_sms_skipped_without_owner_phone(self, db):
        """Without OWNER_PHONE, SMS step should be silently skipped."""
        result = await orchestrate_hatch("no-sms-fly", db=db)
        assert result.hatch_sms_sent is False
        assert not any("SMS" in e for e in result.errors)


class TestHatchGracefulDegradation:
    async def test_matrix_skipped_without_secret(self, db):
        """Matrix provision skips gracefully without Synapse secret."""
        result = await orchestrate_hatch("matrix-fly", db=db)
        assert result.matrix_provisioned is False

    async def test_hatch_completes_despite_matrix_failure(self, db):
        """Hatch should complete even when Matrix provisioning fails."""
        result = await orchestrate_hatch("resilient-fly", db=db)
        # Matrix fails but everything else succeeds
        assert result.passport_id.startswith("ET-")
        assert result.mail_provisioned is True
        assert result.phone_provisioned is True
        assert result.neural_fingerprint != ""

    async def test_errors_are_non_fatal(self, db):
        """Errors from failed steps should be collected but not crash the hatch."""
        result = await orchestrate_hatch("error-fly", db=db)
        # At minimum Matrix will error (no Synapse secret)
        assert any("Matrix" in e for e in result.errors)
        # But the hatch still completed
        assert result.passport_id != ""
        assert result.mail_provisioned is True


class TestHatchResultDefaults:
    def test_defaults(self):
        r = HatchResult()
        assert r.agent_name == ""
        assert r.errors == []
        assert r.passport_id == ""
        assert r.matrix_provisioned is False
        assert r.mail_provisioned is False
        assert r.phone_provisioned is False
        assert r.hatch_sms_sent is False

    def test_error_collection_independent(self):
        """Each HatchResult has its own errors list (no shared mutable default)."""
        r1 = HatchResult()
        r2 = HatchResult()
        r1.errors.append("error1")
        assert r2.errors == []
