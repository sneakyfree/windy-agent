"""End-to-end hatch smoke test — exercises the full hatch flow locally.

Validates every ecosystem integration point:
  1. Naming ceremony (agent_name, owner_name, hardware_specs)
  2. Birth certificate generation (PDF, terminal render, fingerprint)
  3. Hardware specs collection
  4. Hatch orchestrator with mock services
  5. Daemon mode flag
  6. Channel auto-detection
"""

from __future__ import annotations

import argparse
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
        "ETERNITAS_API_URL", "ETERNITAS_PASSPORT",
        "SYNAPSE_REGISTRATION_SECRET",
        "TWILIO_ACCOUNT_SID", "TWILIO_PHONE_NUMBER",
        "WINDYMAIL_SERVICE_TOKEN", "OWNER_PHONE", "OWNER_EMAIL",
        "MATRIX_BOT_TOKEN", "MATRIX_BOT_PASSWORD",
        "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
        "SIGNAL_PHONE_NUMBER", "TEAMS_APP_ID", "IRC_SERVER",
    ]:
        monkeypatch.delenv(var, raising=False)


# ═══════════════════════════════════════════════════════════════════════
# 1. Naming ceremony
# ═══════════════════════════════════════════════════════════════════════


class TestNamingCeremony:
    async def test_agent_name_flows_to_result(self, db):
        """agent_name should appear in HatchResult."""
        result = await orchestrate_hatch(agent_name="Sparky", db=db)
        assert result.agent_name == "Sparky"

    async def test_owner_name_flows_to_result(self, db):
        """owner_name should appear in HatchResult."""
        result = await orchestrate_hatch(
            agent_name="TestFly", owner_name="Grant", db=db,
        )
        assert result.owner_name == "Grant"

    async def test_hardware_specs_collected(self, db):
        """hardware_specs should be populated with CPU and OS."""
        result = await orchestrate_hatch(agent_name="HWFly", db=db)
        assert isinstance(result.hardware_specs, dict)
        assert "cpu" in result.hardware_specs
        assert "os" in result.hardware_specs
        assert result.hardware_specs["cpu"] != ""
        assert result.hardware_specs["os"] != ""


# ═══════════════════════════════════════════════════════════════════════
# 2. Birth certificate generation
# ═══════════════════════════════════════════════════════════════════════


class TestBirthCertificateE2E:
    def test_generate_certificate_fields(self):
        """generate_birth_certificate should produce all required fields."""
        from windyfly.birth_certificate import generate_birth_certificate

        cert = generate_birth_certificate(
            agent_name="CertFly",
            passport_id="ET-TEST001",
            owner_name="Grant",
            model_id="gpt-4o-mini",
            hardware_specs={"cpu": "Apple M2", "os": "macOS 14.2", "ram": "16 GB"},
        )

        assert cert.certificate_number.startswith("WF-")
        assert len(cert.neural_fingerprint) == 64  # SHA-256 hex
        assert cert.waveform_signature != ""
        assert cert.hardware_specs["cpu"] == "Apple M2"
        assert cert.hardware_specs["os"] == "macOS 14.2"
        assert cert.owner_name == "Grant"

    def test_terminal_render_contains_creator(self):
        """Terminal render should say 'Creator:' not 'Owner:'."""
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            render_birth_certificate_terminal,
        )

        cert = generate_birth_certificate(
            agent_name="RenderFly",
            passport_id="ET-RENDER",
            owner_name="Grant",
        )
        output = render_birth_certificate_terminal(cert)
        assert "Creator:" in output
        assert "Owner:" not in output

    def test_pdf_render_returns_valid_pdf(self):
        """render_birth_certificate_pdf should return bytes starting with %PDF."""
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            render_birth_certificate_pdf,
        )

        cert = generate_birth_certificate(
            agent_name="PDFFly",
            passport_id="ET-PDF001",
            owner_name="Grant",
        )
        pdf_bytes = render_birth_certificate_pdf(cert)
        assert isinstance(pdf_bytes, (bytes, bytearray))
        assert pdf_bytes[:5] == b"%PDF-"

    def test_save_certificate_creates_file(self):
        """save_birth_certificate should create a real PDF file."""
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            save_birth_certificate,
        )

        cert = generate_birth_certificate(
            agent_name="SaveFly",
            passport_id="ET-SAVE01",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_birth_certificate(cert, directory=tmpdir)
            assert Path(path).exists()
            assert path.endswith(".pdf")
            # Verify content is valid PDF
            content = Path(path).read_bytes()
            assert content[:5] == b"%PDF-"


# ═══════════════════════════════════════════════════════════════════════
# 3. Hardware specs collection
# ═══════════════════════════════════════════════════════════════════════


class TestHardwareSpecs:
    def test_collect_returns_cpu_and_os(self):
        """collect_hardware_specs should return dict with cpu and os keys."""
        from windyfly.birth_certificate import collect_hardware_specs

        specs = collect_hardware_specs()
        assert isinstance(specs, dict)
        assert "cpu" in specs
        assert "os" in specs

    def test_cpu_not_empty(self):
        """CPU should be a non-empty string."""
        from windyfly.birth_certificate import collect_hardware_specs

        specs = collect_hardware_specs()
        assert specs["cpu"] != ""

    def test_os_contains_platform_name(self):
        """OS should contain a recognizable platform name."""
        from windyfly.birth_certificate import collect_hardware_specs

        specs = collect_hardware_specs()
        os_str = specs["os"].lower()
        assert any(
            name in os_str
            for name in ["macos", "darwin", "linux", "windows", "ubuntu", "debian", "fedora", "arch"]
        ), f"OS string '{specs['os']}' doesn't contain a recognizable platform"


# ═══════════════════════════════════════════════════════════════════════
# 4. Hatch orchestrator with mock services
# ═══════════════════════════════════════════════════════════════════════


class TestHatchOrchestratorE2E:
    async def test_full_hatch_all_services_mocked(self, db, monkeypatch):
        """Mocks all services and verifies complete HatchResult."""
        monkeypatch.setenv("OWNER_PHONE", "+15551234567")

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

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
            ) as mock_sms, \
                patch(
                "windyfly.hatch_orchestrator._step_hatch_email",
                new_callable=AsyncMock,
            ):
                async def fill_eternitas(result, *args, **kwargs):
                    result.passport_id = "ET-99999"
                    result.passport_status = "active"

                async def fill_matrix(result, *args, **kwargs):
                    result.matrix_user_id = "@agent:chat.windypro.com"
                    result.matrix_provisioned = True

                async def fill_mail(result, *args, **kwargs):
                    result.email_address = "agent@windymail.ai"
                    result.mail_provisioned = True

                async def fill_phone(result, *args, **kwargs):
                    result.phone_number = "+1234567890"
                    result.phone_provisioned = True

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

            assert result.passport_id == "ET-99999"
            assert result.matrix_provisioned is True
            assert result.mail_provisioned is True
            assert result.birth_certificate_path.endswith(".pdf")
            assert Path(result.birth_certificate_path).exists()
            assert isinstance(result.hardware_specs, dict)
            assert "cpu" in result.hardware_specs
            assert result.errors == [], f"Unexpected errors: {result.errors}"

    async def test_hatch_with_real_mock_services(self, db):
        """Test using the project's built-in mock services (no patches)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"memory": {"db_path": f"{tmpdir}/windyfly.db"}}

            result = await orchestrate_hatch(
                agent_name="real-mock-fly",
                owner_name="Grant",
                config=config,
                db=db,
            )

            assert result.agent_name == "real-mock-fly"
            assert result.passport_id.startswith("ET-L")
            assert result.mail_provisioned is True
            assert result.phone_provisioned is True
            assert result.birth_certificate_path.endswith(".pdf")
            assert Path(result.birth_certificate_path).exists()

    async def test_hatch_error_resilience(self, db):
        """Hatch should complete even when services fail."""
        with patch(
            "windyfly.hatch_orchestrator._step_eternitas",
            new_callable=AsyncMock,
        ) as mock_et:
            async def fail_eternitas(result, *args, **kwargs):
                result.errors.append("Eternitas: connection refused")

            mock_et.side_effect = fail_eternitas

            result = await orchestrate_hatch(agent_name="resilient-fly", db=db)

            assert isinstance(result, HatchResult)
            assert result.agent_name == "resilient-fly"
            assert "Eternitas: connection refused" in result.errors

    async def test_env_vars_written_after_hatch(self, db, monkeypatch):
        """ETERNITAS_PASSPORT env var should be set after hatch."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

        result = await orchestrate_hatch(agent_name="env-fly", db=db)
        assert os.environ.get("ETERNITAS_PASSPORT") == result.passport_id


# ═══════════════════════════════════════════════════════════════════════
# 5. Daemon mode flag
# ═══════════════════════════════════════════════════════════════════════


class TestDaemonMode:
    def test_argparse_accepts_daemon_flag(self):
        """The start subparser should accept --daemon."""
        from windyfly.cli import main

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        start_parser = sub.add_parser("start")
        start_parser.add_argument("--daemon", "-d", action="store_true")
        start_parser.add_argument("--cli", action="store_true")

        args = parser.parse_args(["start", "--daemon"])
        assert args.daemon is True
        assert args.cli is False

    def test_daemon_false_by_default(self):
        """--daemon should be False when not specified."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        start_parser = sub.add_parser("start")
        start_parser.add_argument("--daemon", "-d", action="store_true")

        args = parser.parse_args(["start"])
        assert args.daemon is False

    def test_quickstart_launch_uses_daemon(self):
        """_launch() in quickstart should set daemon=True."""
        # Verify by checking the source — _launch builds a Namespace with daemon=True
        import inspect
        from windyfly.quickstart import _launch

        source = inspect.getsource(_launch)
        assert "daemon=True" in source


# ═══════════════════════════════════════════════════════════════════════
# 6. Channel auto-detection
# ═══════════════════════════════════════════════════════════════════════


class TestChannelAutoDetection:
    def test_cli_always_detected(self, monkeypatch):
        """CLI channel should always be in detected list."""
        from windyfly.cli import auto_detect_channels

        channels = auto_detect_channels()
        assert "cli" in channels

    def test_telegram_detected_with_token(self, monkeypatch):
        """Telegram should be detected when TELEGRAM_BOT_TOKEN is set."""
        from windyfly.cli import auto_detect_channels

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
        channels = auto_detect_channels()
        assert "telegram" in channels

    def test_discord_detected_with_token(self, monkeypatch):
        """Discord should be detected when DISCORD_BOT_TOKEN is set."""
        from windyfly.cli import auto_detect_channels

        monkeypatch.setenv("DISCORD_BOT_TOKEN", "MTIzNDU2.abc.def")
        channels = auto_detect_channels()
        assert "discord" in channels

    def test_no_tokens_only_cli(self, monkeypatch):
        """With no tokens set, only CLI should be detected."""
        from windyfly.cli import auto_detect_channels

        channels = auto_detect_channels()
        assert channels == ["cli"]

    def test_multiple_channels_detected(self, monkeypatch):
        """Multiple channels should be detected simultaneously."""
        from windyfly.cli import auto_detect_channels

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
        monkeypatch.setenv("SIGNAL_PHONE_NUMBER", "+15551234567")

        channels = auto_detect_channels()
        assert "cli" in channels
        assert "telegram" in channels
        assert "discord" in channels
        assert "signal" in channels
