"""Tests for the Eternitas bot registry."""

from __future__ import annotations

import asyncio

import pytest

from windyfly.eternitas.models import (
    BotIdentity,
    EternitasPassport,
    RegistrationRequest,
    RevocationResult,
)
from windyfly.eternitas.mock import MockEternitasClient
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def client(db):
    return MockEternitasClient(db)


class TestRegistration:
    async def test_register_new_bot(self, client):
        req = RegistrationRequest(agent_name="test-fly", owner_id="owner-1")
        passport = await client.register(req)

        assert passport.passport_id.startswith("ET-L")
        assert passport.agent_name == "test-fly"
        assert passport.owner_id == "owner-1"
        assert passport.status == "active"

    async def test_register_idempotent(self, client):
        """Registering the same agent twice returns the same passport."""
        req = RegistrationRequest(agent_name="same-fly")
        p1 = await client.register(req)
        p2 = await client.register(req)
        assert p1.passport_id == p2.passport_id

    async def test_register_different_agents(self, client):
        """Different agents get different passport IDs."""
        p1 = await client.register(RegistrationRequest(agent_name="fly-a"))
        p2 = await client.register(RegistrationRequest(agent_name="fly-b"))
        assert p1.passport_id != p2.passport_id

    async def test_passport_id_format(self, client):
        """Passport IDs have the format ET-LXXXXX."""
        req = RegistrationRequest(agent_name="format-test")
        passport = await client.register(req)
        assert passport.passport_id.startswith("ET-L")
        # The numeric part should be zero-padded to 5 digits
        num_part = passport.passport_id.split("ET-L")[1]
        assert len(num_part) == 5
        assert num_part.isdigit()


class TestVerify:
    async def test_verify_active_passport(self, client):
        req = RegistrationRequest(agent_name="verify-fly")
        passport = await client.register(req)

        verified = await client.verify(passport.passport_id)
        assert verified is not None
        assert verified.passport_id == passport.passport_id
        assert verified.status == "active"

    async def test_verify_nonexistent(self, client):
        result = await client.verify("ET-L99999")
        assert result is None

    async def test_verify_revoked(self, client):
        req = RegistrationRequest(agent_name="revoke-verify-fly")
        passport = await client.register(req)
        await client.revoke(passport.passport_id)

        verified = await client.verify(passport.passport_id)
        assert verified is not None
        assert verified.status == "revoked"


class TestLookup:
    async def test_lookup_existing(self, client):
        req = RegistrationRequest(agent_name="lookup-fly", owner_id="owner-x")
        await client.register(req)

        identity = await client.lookup("lookup-fly")
        assert identity is not None
        assert isinstance(identity, BotIdentity)
        assert identity.agent_name == "lookup-fly"

    async def test_lookup_nonexistent(self, client):
        result = await client.lookup("ghost-fly")
        assert result is None


class TestRevocation:
    async def test_revoke_active(self, client):
        req = RegistrationRequest(agent_name="revoke-fly")
        passport = await client.register(req)

        result = await client.revoke(passport.passport_id)
        assert result.revoked is True
        assert result.passport_id == passport.passport_id

    async def test_revoke_nonexistent(self, client):
        result = await client.revoke("ET-L00000")
        assert result.revoked is False
        assert result.error != ""

    async def test_revoke_cascade_reports_services(self, client):
        """Revocation should report which services were torn down."""
        req = RegistrationRequest(agent_name="cascade-fly")
        passport = await client.register(req)

        # Add some services
        await client.update_services(passport.passport_id, {
            "matrix": "@cascade-fly:chat.windypro.com",
            "mail": "cascade-fly@windymail.ai",
            "phone": "+15550001234",
        })

        result = await client.revoke(passport.passport_id)
        assert result.revoked is True
        assert set(result.services_torn_down) == {"matrix", "mail", "phone"}

    async def test_lookup_after_revoke_returns_none(self, client):
        """Revoked bots should not appear in lookup."""
        req = RegistrationRequest(agent_name="gone-fly")
        passport = await client.register(req)
        await client.revoke(passport.passport_id)

        result = await client.lookup("gone-fly")
        assert result is None


class TestUpdateServices:
    async def test_update_services(self, client):
        req = RegistrationRequest(agent_name="service-fly")
        passport = await client.register(req)

        updated = await client.update_services(passport.passport_id, {
            "matrix": "@service-fly:chat.windypro.com",
        })
        assert "matrix" in updated.provisioned_services

    async def test_update_services_merge(self, client):
        """Subsequent updates should merge, not replace."""
        req = RegistrationRequest(agent_name="merge-fly")
        passport = await client.register(req)

        await client.update_services(passport.passport_id, {"matrix": "user1"})
        updated = await client.update_services(passport.passport_id, {"mail": "addr1"})

        assert "matrix" in updated.provisioned_services
        assert "mail" in updated.provisioned_services

    async def test_update_nonexistent_raises(self, client):
        with pytest.raises(ValueError):
            await client.update_services("ET-L00000", {"x": "y"})


class TestModels:
    def test_registration_request_validation(self):
        """Agent name is required and non-empty."""
        with pytest.raises(Exception):
            RegistrationRequest(agent_name="")

    def test_passport_defaults(self):
        p = EternitasPassport(passport_id="ET-L00001", agent_name="test")
        assert p.status == "active"
        assert p.provisioned_services == {}

    def test_revocation_result_defaults(self):
        r = RevocationResult(passport_id="ET-L00001")
        assert r.revoked is False
        assert r.services_torn_down == []
