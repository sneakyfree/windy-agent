"""Contract tests for Windy Pro API tools.

Verifies that each tool function sends correct paths, headers, and params,
and handles error cases gracefully (connection refused, 401, 500).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.tools.windy_api import (
    get_clone_status,
    get_recordings,
    get_translation_history,
    translate_text,
)

PRO_BASE = "http://localhost:8098"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("WINDY_API_URL", PRO_BASE)
    monkeypatch.setenv("WINDY_JWT", "test_jwt_token_123")


# --- Translation history ---


class TestTranslationHistoryContract:
    @respx.mock
    def test_correct_path_and_auth(self):
        route = respx.get(f"{PRO_BASE}/api/v1/user/history").mock(
            return_value=httpx.Response(200, json={
                "translations": [
                    {"id": "t1", "text": "Hello", "source_lang": "en", "target_lang": "es"}
                ]
            })
        )

        result = get_translation_history(limit=5)

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test_jwt_token_123"
        assert "limit" in str(request.url)
        assert result["translations"][0]["id"] == "t1"

    @respx.mock
    def test_connection_refused(self):
        respx.get(f"{PRO_BASE}/api/v1/user/history").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = get_translation_history()
        assert "error" in result
        assert "not available" in result["error"].lower()
        assert result["translations"] == []

    @respx.mock
    def test_401_unauthorized(self):
        respx.get(f"{PRO_BASE}/api/v1/user/history").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        result = get_translation_history()
        assert "error" in result

    @respx.mock
    def test_500_server_error(self):
        respx.get(f"{PRO_BASE}/api/v1/user/history").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        result = get_translation_history()
        assert "error" in result


# --- Recordings ---


class TestRecordingsContract:
    @respx.mock
    def test_correct_path_and_params(self):
        route = respx.get(f"{PRO_BASE}/api/v1/recordings/list").mock(
            return_value=httpx.Response(200, json={
                "recordings": [
                    {"id": "r1", "bundleId": "b1", "durationSeconds": 120}
                ],
                "total": 1,
            })
        )

        result = get_recordings(limit=5, query="meeting")

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test_jwt_token_123"
        url_str = str(request.url)
        assert "limit=5" in url_str
        assert "q=meeting" in url_str
        assert result["recordings"][0]["id"] == "r1"

    @respx.mock
    def test_empty_recordings_graceful(self):
        """Empty recordings returns friendly message about local storage."""
        respx.get(f"{PRO_BASE}/api/v1/recordings/list").mock(
            return_value=httpx.Response(200, json={"recordings": [], "total": 0})
        )

        result = get_recordings()
        assert result["recordings"] == []
        assert "message" in result
        assert "local" in result["message"].lower()

    @respx.mock
    def test_connection_refused(self):
        respx.get(f"{PRO_BASE}/api/v1/recordings/list").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = get_recordings()
        assert "error" in result
        assert result["recordings"] == []


# --- Clone status ---


class TestCloneStatusContract:
    @respx.mock
    def test_correct_path(self):
        route = respx.get(f"{PRO_BASE}/api/v1/clone/training-data").mock(
            return_value=httpx.Response(200, json={
                "bundles": [],
                "total": 0,
            })
        )

        result = get_clone_status()

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test_jwt_token_123"
        assert result["total"] == 0

    @respx.mock
    def test_connection_refused_graceful(self):
        """Connection error returns friendly message, not crash."""
        respx.get(f"{PRO_BASE}/api/v1/clone/training-data").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = get_clone_status()
        assert result["available"] is False
        assert "not available" in result["message"]

    @respx.mock
    def test_404_endpoint_not_deployed(self):
        """404 returns friendly message — endpoint may not exist yet."""
        respx.get(f"{PRO_BASE}/api/v1/clone/training-data").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        result = get_clone_status()
        assert result["available"] is False

    @respx.mock
    def test_500_server_error(self):
        respx.get(f"{PRO_BASE}/api/v1/clone/training-data").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        result = get_clone_status()
        assert "error" in result or result.get("available") is False


# --- Translate text ---


class TestTranslateContract:
    @respx.mock
    def test_correct_path_method_and_body(self):
        route = respx.post(f"{PRO_BASE}/api/v1/translate/text").mock(
            return_value=httpx.Response(200, json={"translated_text": "hola"})
        )

        result = translate_text("hello", "en", "es")

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test_jwt_token_123"

        import json
        body = json.loads(request.content)
        assert body["text"] == "hello"
        assert body["source_lang"] == "en"
        assert body["target_lang"] == "es"

        assert result["translated_text"] == "hola"

    @respx.mock
    def test_connection_refused(self):
        respx.post(f"{PRO_BASE}/api/v1/translate/text").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = translate_text("hello", "en", "es")
        assert "error" in result
        assert "not available" in result["error"].lower()

    @respx.mock
    def test_401_unauthorized(self):
        respx.post(f"{PRO_BASE}/api/v1/translate/text").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        result = translate_text("hello", "en", "es")
        assert "error" in result
