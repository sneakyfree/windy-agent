"""Tests for gateway security hardening.

Validates rate limiting, input validation, localhost restriction,
and TOML/env injection prevention on gateway setup routes.

These tests verify the gateway hardening at the source code level
(static analysis) since starting the actual Bun server requires
the live gateway tests.
"""

from __future__ import annotations

import re
from pathlib import Path


GATEWAY_SERVER = (
    Path(__file__).resolve().parent.parent / "gateway" / "src" / "server.ts"
)


class TestGatewayRateLimiting:
    def test_validate_key_has_rate_limit(self):
        """validate-key route should check isRateLimited before processing."""
        src = GATEWAY_SERVER.read_text()
        # Find the validate-key route handler
        idx = src.find("/api/setup/validate-key")
        assert idx > 0
        # Rate limit check should appear within 500 chars after route match
        chunk = src[idx:idx + 600]
        assert "isRateLimited" in chunk, (
            "validate-key route is missing rate limit check"
        )

    def test_rate_limit_returns_429(self):
        """Rate limit should return HTTP 429."""
        src = GATEWAY_SERVER.read_text()
        assert "status: 429" in src

    def test_rate_limit_function_exists(self):
        """isRateLimited function should be defined."""
        src = GATEWAY_SERVER.read_text()
        assert "function isRateLimited" in src

    def test_rate_limit_window_defined(self):
        """Rate limit window constant should be defined."""
        src = GATEWAY_SERVER.read_text()
        assert "RATE_LIMIT_WINDOW_MS" in src
        # P1-S5+S6+O5-auth: RATE_LIMIT_MAX became a per-bucket map.
        assert "RATE_LIMITS" in src


class TestGatewayInputValidation:
    def test_finalize_validates_model(self):
        """finalize route should validate model input."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/finalize")
        assert idx > 0
        chunk = src[idx:idx + 2000]
        assert "Invalid model" in chunk or "body.model" in chunk

    def test_finalize_validates_preset(self):
        """finalize route should validate preset against whitelist."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/finalize")
        chunk = src[idx:idx + 2000]
        assert "VALID_PRESETS" in chunk, (
            "finalize route should validate preset against VALID_PRESETS whitelist"
        )

    def test_finalize_validates_api_keys_type(self):
        """finalize route should validate api_keys is an object."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/finalize")
        chunk = src[idx:idx + 2000]
        assert "typeof body.api_keys" in chunk

    def test_finalize_rejects_unknown_key_names(self):
        """finalize route should reject unknown API key names."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/finalize")
        chunk = src[idx:idx + 2500]
        assert "Unknown API key" in chunk

    def test_finalize_rejects_long_key_values(self):
        """finalize route should reject excessively long key values."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/finalize")
        chunk = src[idx:idx + 2500]
        assert "too long" in chunk

    def test_validate_key_rejects_unknown_key_name(self):
        """validate-key route should reject unknown key names."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/validate-key")
        chunk = src[idx:idx + 1500]
        assert "VALID_KEY_NAMES" in chunk

    def test_validate_key_rejects_short_key_value(self):
        """validate-key route should reject very short key values."""
        src = GATEWAY_SERVER.read_text()
        idx = src.find("/api/setup/validate-key")
        chunk = src[idx:idx + 1500]
        assert "key_value.length" in chunk or "key_value.length < 5" in chunk


class TestGatewayLocalhostRestriction:
    def test_setup_routes_have_localhost_guard(self):
        """All /api/setup/ routes should be restricted to localhost."""
        src = GATEWAY_SERVER.read_text()
        assert "isLocalhostRequest" in src

    def test_localhost_guard_returns_403(self):
        """Non-localhost setup requests should return 403."""
        src = GATEWAY_SERVER.read_text()
        # Find the setup route guard (not the function definition)
        idx = src.find('path.startsWith("/api/setup/")')
        assert idx > 0, "Localhost guard for /api/setup/ routes not found"
        chunk = src[idx:idx + 300]
        assert "403" in chunk

    def test_localhost_function_checks_peer_ip(self):
        """isLocalhostRequest should check hostname against localhost variants."""
        src = GATEWAY_SERVER.read_text()
        assert "127.0.0.1" in src
        assert "::1" in src
        assert "requestIP(req)" in src


class TestGatewayTOMLInjection:
    def test_sanitize_function_exists(self):
        """sanitizeForToml function should be defined."""
        src = GATEWAY_SERVER.read_text()
        assert "function sanitizeForToml" in src

    def test_model_is_sanitized(self):
        """Model name should be sanitized before TOML interpolation."""
        src = GATEWAY_SERVER.read_text()
        assert "safeModel" in src

    def test_preset_is_whitelisted(self):
        """Preset should use whitelist validation, not just sanitization."""
        src = GATEWAY_SERVER.read_text()
        assert "VALID_PRESETS.includes(body.preset)" in src

    def test_valid_presets_list_complete(self):
        """VALID_PRESETS should contain all 8 presets."""
        src = GATEWAY_SERVER.read_text()
        for preset in ["buddy", "engineer", "powerhouse", "coder",
                        "friend", "writer", "researcher", "silent"]:
            assert f'"{preset}"' in src

    def test_valid_key_names_list_complete(self):
        """VALID_KEY_NAMES should contain all 6 provider keys."""
        src = GATEWAY_SERVER.read_text()
        for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
                     "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY"]:
            assert f'"{key}"' in src


class TestGatewayCORSHeaders:
    def test_cors_headers_present(self):
        """CORS headers should be set on all API responses."""
        src = GATEWAY_SERVER.read_text()
        assert "Access-Control-Allow-Origin" in src

    def test_preflight_handled(self):
        """OPTIONS preflight requests should be handled."""
        src = GATEWAY_SERVER.read_text()
        assert 'req.method === "OPTIONS"' in src
        assert "status: 204" in src

    def test_error_handler_exists(self):
        """Global error handler should return JSON error, not stack trace."""
        src = GATEWAY_SERVER.read_text()
        assert "status: 500" in src


class TestGateway404Handler:
    def test_has_404_response(self):
        """Gateway should return 404 JSON for unknown paths."""
        src = GATEWAY_SERVER.read_text()
        assert "status: 404" in src
        assert '"Not found"' in src
