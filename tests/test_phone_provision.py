"""Tests for phone number provisioning."""

from __future__ import annotations

import pytest

from windyfly.memory.database import Database
from windyfly.phone_provision import (
    PhoneProvisionResult,
    provision_phone,
    release_phone,
)


@pytest.fixture(autouse=True)
def _no_ambient_twilio(monkeypatch):
    """Make provisioning tests hermetic.

    provision_phone() keys off ambient environment: TWILIO_PHONE_NUMBER
    short-circuits to that number, and TWILIO_ACCOUNT_SID+TWILIO_AUTH_TOKEN
    trigger a REAL number purchase. A developer machine with any of these
    exported (Grant's Mac has TWILIO_PHONE_NUMBER set) made the mock-mode
    tests fail with is_mock=False — and, worse, a machine with real creds
    could have attempted a live purchase during a test run. Clear them so
    these tests exercise the mock path deterministically on any machine.
    """
    for var in (
        "TWILIO_PHONE_NUMBER",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


class TestMockProvisioning:
    async def test_provision_assigns_number(self, db):
        result = await provision_phone("ET-L00001", "test-fly", db=db)
        assert result.success is True
        assert result.phone_number.startswith("+1555")
        assert result.is_mock is True

    async def test_provision_idempotent(self, db):
        r1 = await provision_phone("ET-L00001", "fly", db=db)
        r2 = await provision_phone("ET-L00001", "fly", db=db)
        assert r1.phone_number == r2.phone_number

    async def test_different_agents_different_numbers(self, db):
        r1 = await provision_phone("ET-L00001", "fly-a", db=db)
        r2 = await provision_phone("ET-L00002", "fly-b", db=db)
        assert r1.phone_number != r2.phone_number

    async def test_release_and_reassign(self, db):
        r1 = await provision_phone("ET-L00001", "fly", db=db)
        released = await release_phone("ET-L00001", r1.phone_number, db=db)
        assert released is True

    async def test_provision_without_db_or_twilio(self):
        """Without Twilio creds or DB, provisioning should fail gracefully."""
        import os
        os.environ.pop("TWILIO_ACCOUNT_SID", None)
        os.environ.pop("TWILIO_PHONE_NUMBER", None)
        result = await provision_phone("ET-L00001", "fly", db=None)
        assert result.success is False

    async def test_existing_env_number(self, db, monkeypatch):
        """If TWILIO_PHONE_NUMBER is already set, use it."""
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15559999999")
        result = await provision_phone("ET-L00001", "fly", db=db)
        assert result.success is True
        assert result.phone_number == "+15559999999"


class TestPhoneProvisionResult:
    def test_defaults(self):
        r = PhoneProvisionResult(success=False)
        assert r.phone_number == ""
        assert r.is_mock is False
