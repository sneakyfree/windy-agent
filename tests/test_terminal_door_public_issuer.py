"""The terminal door hatches through the CONSUMER door, like the other two.

`windy go` used to mint via `POST /api/v1/bots/register`, which demands an
operator API key belonging to an already-VERIFIED operator. That key is blank
in `.env.example`, in `.env.production.example`, and on Windy 0's live env —
so on a fresh machine the terminal door 401d and produced no passport at all
(verified by running, 2026-07-31, docs/TERMINAL_DOOR_REPORT.md).

`/bots/auto-hatch` is the anonymous human door the browser and mobile lanes
already come through, and its own route docstring calls it "the normie path
for `windy go` hatches". Three doors, one hallway, ONE issuer door.

`/bots/register` is untouched — it remains the programmatic/enterprise path
for already-verified operators, per 00-THE-CONTRACT.md.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.eternitas.client import EternitasClient
from windyfly.eternitas.models import RegistrationRequest
from windyfly.hatch_orchestrator import orchestrate_hatch
from windyfly.memory.database import Database

ETERNITAS_BASE = "https://api.eternitas.test"

_PASSPORT_RESPONSE = {
    "passport": "ET26-AUTO-0001",
    "name": "Terminal Fly",
    "ept_token": "jwt_token_123",
    "api_key": "et_live_abc123",
    "status": "active",
    "trust_score": 70,
    "certificate": {"certificate_no": "ET-AUTO0001", "passport": "ET26-AUTO-0001"},
}


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "ETERNITAS_PASSPORT",
        "ETERNITAS_OPERATOR_KEY",
        "ETERNITAS_OPERATOR_JWT",
        "ETERNITAS_TURNSTILE_TOKEN",
        "WINDY_JWT",
        "WINDY_IDENTITY_ID",
        "SYNAPSE_REGISTRATION_SECRET",
        "WINDYMAIL_SERVICE_TOKEN",
        "OWNER_PHONE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ETERNITAS_URL", ETERNITAS_BASE)


class TestTheHatchUsesTheConsumerDoor:
    @respx.mock
    async def test_hatch_mints_via_auto_hatch_not_register(self, db):
        """The regression: a hatch with no operator key must still work."""
        auto = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
            return_value=httpx.Response(201, json=_PASSPORT_RESPONSE)
        )
        register = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid API key"})
        )
        respx.get(url__startswith=f"{ETERNITAS_BASE}/api/v1/certificates/").mock(
            return_value=httpx.Response(404)
        )

        result = await orchestrate_hatch("Terminal Fly", owner_name="Grant", db=db)

        assert auto.called, "the hatch did not use the consumer door"
        assert not register.called, "the hatch used the enterprise door"
        assert result.passport_id == "ET26-AUTO-0001"
        assert result.certificate_number == "ET-AUTO0001"

    @respx.mock
    async def test_no_operator_api_key_is_sent(self, db, monkeypatch):
        """Even when a key exists, the consumer door does not use it."""
        monkeypatch.setenv("ETERNITAS_OPERATOR_KEY", "et_op_should_not_be_used")
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
            return_value=httpx.Response(201, json=_PASSPORT_RESPONSE)
        )
        respx.get(url__startswith=f"{ETERNITAS_BASE}/api/v1/certificates/").mock(
            return_value=httpx.Response(404)
        )

        await orchestrate_hatch("Terminal Fly", db=db)

        assert "X-API-Key" not in route.calls.last.request.headers


class TestAutoHatchContract:
    @respx.mock
    async def test_sends_the_flat_consumer_payload(self):
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
            return_value=httpx.Response(201, json=_PASSPORT_RESPONSE)
        )
        client = EternitasClient(api_url=ETERNITAS_BASE)

        await client.auto_hatch(
            RegistrationRequest(
                name="Terminal Fly",
                contact_email="grandma@example.com",
                owner_name="Grandma",
                model_id="claude-opus-5",
                hatch_machine_id="machine-42",
                hatch_timezone="BST",
                hardware_specs={"cpu": "Apple M4"},
            )
        )

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["agent_name"] == "Terminal Fly"
        assert body["creator_name"] == "Grandma"
        assert body["creator_email"] == "grandma@example.com"
        assert body["model_id"] == "claude-opus-5"
        assert body["machine_id"] == "machine-42"
        assert body["hatch_timezone"] == "BST"
        assert body["hardware_specs"] == {"cpu": "Apple M4"}

    @respx.mock
    async def test_operator_jwt_is_sent_when_available(self, monkeypatch):
        """Authenticated callers skip Turnstile and the pro-JWT gate."""
        monkeypatch.setenv("ETERNITAS_OPERATOR_JWT", "operator-jwt-abc")
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
            return_value=httpx.Response(201, json=_PASSPORT_RESPONSE)
        )
        client = EternitasClient(api_url=ETERNITAS_BASE)

        await client.auto_hatch(RegistrationRequest(name="Terminal Fly"))

        assert route.calls.last.request.headers["Authorization"] == "Bearer operator-jwt-abc"

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, "account.windyword.ai"),
            (403, "human"),
            (429, "5 hatches per hour"),
        ],
    )
    @respx.mock
    async def test_gate_responses_become_human_sentences(self, status, expected):
        """A grandma must not be shown a raw HTTP status."""
        respx.post(f"{ETERNITAS_BASE}/api/v1/bots/auto-hatch").mock(
            return_value=httpx.Response(status, json={"detail": "nope"})
        )
        client = EternitasClient(api_url=ETERNITAS_BASE)

        with pytest.raises(RuntimeError) as excinfo:
            await client.auto_hatch(RegistrationRequest(name="Terminal Fly"))

        assert expected in str(excinfo.value)


class TestEnterpriseDoorIsUntouched:
    @respx.mock
    async def test_register_still_posts_to_register_with_its_key(self):
        """/bots/register remains for already-verified operators."""
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(201, json=_PASSPORT_RESPONSE)
        )
        client = EternitasClient(api_url=ETERNITAS_BASE, operator_key="et_op_enterprise")

        await client.register(RegistrationRequest(name="Enterprise Fly"))

        assert route.calls.last.request.headers["X-API-Key"] == "et_op_enterprise"
