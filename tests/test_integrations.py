"""Tests for Windy ecosystem integration stubs."""

from __future__ import annotations

import pytest

from windyfly.integrations.windy_clone import CloneStatus, get_clone_status
from windyfly.integrations.windy_cloud import BackupResult, SyncStatus, backup_database, sync_status
from windyfly.integrations.windy_traveler import TranslationResult, get_supported_languages, translate_text
from windyfly.integrations.windy_word import Recording, search_recordings, get_recording
from windyfly.integrations.push_gateway import PushResult, send_push
from windyfly.integrations.contact_discovery import DiscoveredContact, discover_contacts, hash_phone


class TestWindyClone:
    async def test_no_config_returns_error(self, monkeypatch):
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        status = await get_clone_status()
        assert isinstance(status, CloneStatus)
        assert status.is_available is False
        assert "not configured" in status.error

    def test_defaults(self):
        s = CloneStatus()
        assert s.training_progress == 0.0
        assert s.is_ready is False


class TestWindyCloud:
    async def test_backup_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_URL", raising=False)
        result = await backup_database("data/windyfly.db")
        assert isinstance(result, BackupResult)
        assert result.success is False

    async def test_sync_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_URL", raising=False)
        status = await sync_status()
        assert isinstance(status, SyncStatus)
        assert status.is_available is False


class TestWindyTraveler:
    async def test_translate_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        result = await translate_text("Hello", "es")
        assert isinstance(result, TranslationResult)
        assert result.success is False

    def test_supported_languages(self):
        langs = get_supported_languages()
        assert "en" in langs
        assert "es" in langs
        assert len(langs) >= 50


class TestWindyWord:
    async def test_search_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        results = await search_recordings("meeting")
        assert results == []

    async def test_get_recording_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        result = await get_recording("rec-123")
        assert result is None

    def test_recording_defaults(self):
        r = Recording()
        assert r.duration_seconds == 0.0


class TestPushGateway:
    async def test_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_PUSH_URL", raising=False)
        result = await send_push("token", "Title", "Body")
        assert isinstance(result, PushResult)
        assert result.success is False


class TestContactDiscovery:
    async def test_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_DISCOVERY_URL", raising=False)
        results = await discover_contacts(["abc123"])
        assert results == []

    def test_hash_phone_deterministic(self):
        h1 = hash_phone("+15551234567")
        h2 = hash_phone("+15551234567")
        assert h1 == h2

    def test_hash_phone_normalizes(self):
        h1 = hash_phone("+1 555 123 4567")
        h2 = hash_phone("+15551234567")
        assert h1 == h2

    def test_hash_phone_different(self):
        h1 = hash_phone("+15551234567")
        h2 = hash_phone("+15559876543")
        assert h1 != h2
