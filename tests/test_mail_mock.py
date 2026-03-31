"""Tests for mock mail server."""

from __future__ import annotations

import pytest

from windyfly.mail_mock import MockMailServer
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def server(db):
    return MockMailServer(db)


class TestMockMailProvisioning:
    async def test_provision_inbox(self, server):
        result = await server.provision_inbox("test-fly", "ET-L00001")
        assert result["email"] == "test-fly@windymail.ai"
        assert result["jmap_token"].startswith("mock-jmap-")
        assert result["smtp_password"] != ""
        assert result["imap_password"] != ""

    async def test_provision_idempotent(self, server):
        r1 = await server.provision_inbox("fly", "ET-L00001")
        r2 = await server.provision_inbox("fly", "ET-L00001")
        assert r1["email"] == r2["email"]

    async def test_provision_normalizes_name(self, server):
        result = await server.provision_inbox("My Cool Fly", "ET-L00001")
        assert result["email"] == "my-cool-fly@windymail.ai"


class TestMockMailSending:
    async def test_send_email(self, server):
        result = await server.send_email(
            "fly@windymail.ai", "user@example.com", "Hello", "Body text"
        )
        assert result["status"] == "sent"
        assert result["message_id"] != ""

    async def test_inbox(self, server):
        await server.send_email("a@x.com", "fly@windymail.ai", "Hi", "Body1")
        await server.send_email("b@x.com", "fly@windymail.ai", "Hey", "Body2")
        inbox = await server.get_inbox("fly@windymail.ai")
        assert len(inbox) == 2

    async def test_sent(self, server):
        await server.send_email("fly@windymail.ai", "a@x.com", "Out", "Going")
        sent = await server.get_sent("fly@windymail.ai")
        assert len(sent) == 1
