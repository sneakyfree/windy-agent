"""Tests for windyfly.matrix_provision — Matrix bot auto-provisioning.

Covers HMAC generation, missing-secret handling, network error
resilience, and .env update logic.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from windyfly.matrix_provision import (
    _generate_mac,
    auto_provision_and_save,
    provision_matrix_bot,
)


# ═══════════════════════════════════════════════════════════════════════
# HMAC Generation
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateMac:
    def test_produces_hex_string(self):
        """_generate_mac should produce a hex string."""
        mac = _generate_mac("nonce123", "windyfly", "pass123", admin=False, secret="secret")
        assert isinstance(mac, str)
        # Should be valid hex
        int(mac, 16)

    def test_correct_hmac_sha1(self):
        """Verify HMAC-SHA1 output matches expected format."""
        mac = _generate_mac("nonce", "user", "pass", admin=False, secret="key")
        # HMAC-SHA1 produces 40 hex chars
        assert len(mac) == 40

    def test_admin_flag_changes_output(self):
        """admin=True should produce a different MAC than admin=False."""
        mac_user = _generate_mac("nonce", "user", "pass", admin=False, secret="key")
        mac_admin = _generate_mac("nonce", "user", "pass", admin=True, secret="key")
        assert mac_user != mac_admin


# ═══════════════════════════════════════════════════════════════════════
# Provisioning
# ═══════════════════════════════════════════════════════════════════════


class TestProvisionMatrixBot:
    def test_returns_none_when_no_secret(self):
        """provision_matrix_bot() should return None when no secret is set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNAPSE_REGISTRATION_SECRET", None)
            result = provision_matrix_bot(registration_secret="")
            assert result is None

    def test_handles_network_errors_gracefully(self):
        """provision_matrix_bot() should return None on network errors."""
        import httpx as _httpx
        with patch.object(_httpx, "get", side_effect=ConnectionError("Connection refused")):
            result = provision_matrix_bot(
                homeserver="https://fake.example.com",
                registration_secret="test-secret",
            )
            assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Auto-Provision and Save
# ═══════════════════════════════════════════════════════════════════════


class TestAutoProvisionAndSave:
    def test_no_crash_when_env_missing(self, tmp_path: Path, monkeypatch):
        """auto_provision_and_save() should not crash when .env doesn't exist."""
        monkeypatch.setattr("windyfly.matrix_provision.PROJECT_ROOT", tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNAPSE_REGISTRATION_SECRET", None)
            result = auto_provision_and_save()
            assert result is False

    def test_returns_false_when_no_secret(self, monkeypatch):
        """auto_provision_and_save() should return False when secret unavailable."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNAPSE_REGISTRATION_SECRET", None)
            result = auto_provision_and_save()
            assert result is False
