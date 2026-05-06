"""Tests for the cloud tool — upload_to_cloud and list_cloud_files.

Covers:
  - Tool registration adds both tools
  - upload returns 'unavailable' when env unset
  - upload returns 'failed' when file doesn't exist
  - upload happy path: POSTs multipart with auth header
  - upload 404 → 'unavailable' (cloud reachable but endpoint missing)
  - list returns 'unavailable' when env unset
  - list happy path: GETs files, returns trimmed slice
  - list normalises both {files: [...]} and bare list responses
  - WINDY_JWT fallback when WINDY_CLOUD_TOKEN unset
  - Boot sequence registers tools.cloud
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.cloud import (
    list_cloud_files,
    register_cloud_tools,
    upload_to_cloud,
)
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.windyword.test")
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "cloud_test_token")


@pytest.fixture
def no_cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("WINDY_CLOUD_URL", "WINDY_CLOUD_TOKEN", "WINDY_JWT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "report.txt"
    p.write_text("Austin loan-officer battle plan\n")
    return p


class TestRegistration:
    def test_registers_both_tools(self) -> None:
        registry = ToolRegistry()
        register_cloud_tools(registry)
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "upload_to_cloud" in names
        assert "list_cloud_files" in names


class TestUploadUnavailable:
    def test_returns_unavailable_when_env_unset(
        self, no_cloud_env: None, sample_file: Path,
    ) -> None:
        result = upload_to_cloud(file_path=str(sample_file))
        assert result["status"] == "unavailable"
        assert "WINDY_CLOUD_URL" in result["error"]

    def test_jwt_fallback_when_only_jwt_set(
        self, monkeypatch: pytest.MonkeyPatch, sample_file: Path,
    ) -> None:
        monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.test")
        monkeypatch.delenv("WINDY_CLOUD_TOKEN", raising=False)
        monkeypatch.setenv("WINDY_JWT", "jwt-token")

        with patch("windyfly.tools.cloud.httpx.post") as mock_post:
            mock_resp = MagicMock(status_code=201)
            mock_resp.json.return_value = {"file_id": "f-1"}
            mock_post.return_value = mock_resp
            result = upload_to_cloud(file_path=str(sample_file))

        assert result["status"] == "uploaded"
        # Auth header used the JWT fallback
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer jwt-token"


class TestUploadValidation:
    def test_missing_file_returns_failed(self, cloud_env: None) -> None:
        result = upload_to_cloud(file_path="/tmp/definitely-not-a-file-xyz123.bin")
        assert result["status"] == "failed"
        assert "not found" in result["error"].lower()

    def test_directory_path_returns_failed(
        self, cloud_env: None, tmp_path: Path,
    ) -> None:
        result = upload_to_cloud(file_path=str(tmp_path))
        assert result["status"] == "failed"
        assert "regular file" in result["error"].lower()


class TestUploadHappyPath:
    @patch("windyfly.tools.cloud.httpx.post")
    def test_uploads_with_correct_url_and_auth(
        self, mock_post: MagicMock, cloud_env: None, sample_file: Path,
    ) -> None:
        mock_resp = MagicMock(status_code=201)
        mock_resp.json.return_value = {"file_id": "f-42", "url": "https://..."}
        mock_post.return_value = mock_resp

        result = upload_to_cloud(
            file_path=str(sample_file),
            name="austin-plan.txt",
            description="Loan officer plan",
        )

        assert result["status"] == "uploaded"
        assert result["name"] == "austin-plan.txt"
        assert result["file_id"] == "f-42"
        assert result["size_bytes"] == sample_file.stat().st_size

        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"]["Authorization"] == "Bearer cloud_test_token"
        assert kwargs["data"] == {"description": "Loan officer plan"}
        # files= contains the multipart upload
        assert "file" in kwargs["files"]


class TestUpload404:
    @patch("windyfly.tools.cloud.httpx.post")
    def test_404_maps_to_unavailable(
        self, mock_post: MagicMock, cloud_env: None, sample_file: Path,
    ) -> None:
        mock_resp = MagicMock(status_code=404, text="not found")
        mock_post.return_value = mock_resp

        result = upload_to_cloud(file_path=str(sample_file))
        assert result["status"] == "unavailable"
        assert "endpoint not found" in result["error"].lower()


class TestListInbox:
    def test_returns_unavailable_when_env_unset(self, no_cloud_env: None) -> None:
        result = list_cloud_files()
        assert result["status"] == "unavailable"
        assert result["files"] == []

    @patch("windyfly.tools.cloud.httpx.get")
    def test_normalises_files_array_in_envelope(
        self, mock_get: MagicMock, cloud_env: None,
    ) -> None:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "files": [{"id": f"f-{i}", "name": f"file-{i}"} for i in range(50)],
        }
        mock_get.return_value = mock_resp

        result = list_cloud_files(limit=10)
        assert result["status"] == "ok"
        assert result["count"] == 10
        assert len(result["files"]) == 10

    @patch("windyfly.tools.cloud.httpx.get")
    def test_normalises_bare_list_response(
        self, mock_get: MagicMock, cloud_env: None,
    ) -> None:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = [
            {"id": "f-1", "name": "a"},
            {"id": "f-2", "name": "b"},
        ]
        mock_get.return_value = mock_resp

        result = list_cloud_files()
        assert result["status"] == "ok"
        assert result["count"] == 2

    @patch("windyfly.tools.cloud.httpx.get")
    def test_passes_prefix_and_limit(
        self, mock_get: MagicMock, cloud_env: None,
    ) -> None:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"files": []}
        mock_get.return_value = mock_resp

        list_cloud_files(prefix="austin-", limit=5)
        params = mock_get.call_args.kwargs["params"]
        assert params == {"limit": 5, "prefix": "austin-"}


class TestBootSequenceWiring:
    def test_default_sequence_includes_tools_cloud(self) -> None:
        from windyfly.agent.boot import default_capability_registration_sequence

        sequence = default_capability_registration_sequence()
        names = [s.name for s in sequence]
        assert "tools.cloud" in names
        # Cloud registers right after chat per design.
        cloud_idx = names.index("tools.cloud")
        assert names[cloud_idx - 1] == "tools.chat"
