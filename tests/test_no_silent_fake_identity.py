"""A run must never fabricate an identity Eternitas has never issued.

`get_eternitas_client` used to fall back to `MockEternitasClient` the
moment no Eternitas URL was configured. The ceremony then ran green,
emitted `eternitas.registered`, and returned a passport number the
issuer has never heard of. That is worse than failing, because it looks
like success — and a credential nobody issued is the exact thing this
platform exists to prevent (00-THE-CONTRACT.md: "No lane may fabricate
identity. A lane that cannot reach Eternitas fails loudly.").

The mock stays available for tests and offline development. What is gone
is reaching it by ACCIDENT: it now takes either an explicitly mock URL or
the WINDYFLY_ALLOW_FAKE_IDENTITY opt-in, whose name says what it is.
"""

from __future__ import annotations

import pytest

from windyfly.eternitas.provision import (
    FAKE_IDENTITY_OPTIN_ENV,
    FakeIdentityRefused,
    get_eternitas_client,
)
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def no_eternitas_url(monkeypatch):
    monkeypatch.delenv("ETERNITAS_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)


class TestSilentMockIsImpossible:
    def test_unconfigured_run_refuses_instead_of_faking(self, db, monkeypatch):
        """No URL and no opt-in: refuse loudly rather than mint a fake."""
        monkeypatch.delenv(FAKE_IDENTITY_OPTIN_ENV, raising=False)

        with pytest.raises(FakeIdentityRefused) as excinfo:
            get_eternitas_client(db=db)

        message = str(excinfo.value)
        # The message has to tell a human what to actually do.
        assert "ETERNITAS_URL" in message
        assert FAKE_IDENTITY_OPTIN_ENV in message

    def test_opt_in_flag_still_allows_the_mock(self, db, monkeypatch):
        """Offline development keeps working — it just has to say so."""
        monkeypatch.setenv(FAKE_IDENTITY_OPTIN_ENV, "1")

        from windyfly.eternitas.mock import MockEternitasClient

        assert isinstance(get_eternitas_client(db=db), MockEternitasClient)

    def test_explicit_mock_url_needs_no_flag(self, db, monkeypatch):
        """`mock://local` is already an explicit request for a fake."""
        monkeypatch.delenv(FAKE_IDENTITY_OPTIN_ENV, raising=False)
        monkeypatch.setenv("ETERNITAS_URL", "mock://local")

        from windyfly.eternitas.mock import MockEternitasClient

        assert isinstance(get_eternitas_client(db=db), MockEternitasClient)

    def test_real_url_is_untouched(self, db, monkeypatch):
        """A configured issuer is the normal path and must not be gated."""
        monkeypatch.delenv(FAKE_IDENTITY_OPTIN_ENV, raising=False)
        monkeypatch.setenv("ETERNITAS_URL", "https://api.eternitas.ai")

        from windyfly.eternitas.client import EternitasClient

        client = get_eternitas_client(db=db)
        assert isinstance(client, EternitasClient)
        assert client.api_url == "https://api.eternitas.ai"

    def test_config_url_is_untouched(self, db, monkeypatch):
        """windyfly.toml's ecosystem.eternitas_url is the shipped default."""
        monkeypatch.delenv(FAKE_IDENTITY_OPTIN_ENV, raising=False)

        from windyfly.eternitas.client import EternitasClient

        client = get_eternitas_client(
            db=db, config={"ecosystem": {"eternitas_url": "https://api.eternitas.ai"}}
        )
        assert isinstance(client, EternitasClient)


class TestHatchFailsLoudlyRatherThanFaking:
    async def test_hatch_reports_failure_not_a_fake_passport(self, db, monkeypatch):
        """The ceremony must go red, not green-with-an-invented-passport."""
        monkeypatch.delenv(FAKE_IDENTITY_OPTIN_ENV, raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

        from windyfly.hatch_orchestrator import orchestrate_hatch

        events: list[tuple[str, dict]] = []
        result = await orchestrate_hatch(
            "unconfigured-fly", db=db, on_event=lambda e, d: events.append((e, d))
        )

        assert result.passport_id == ""
        assert any("Eternitas" in e for e in result.errors)

        registered = [d for name, d in events if name == "eternitas.registered"]
        assert registered and registered[0]["ok"] is False

        complete = [d for name, d in events if name == "hatch.complete"]
        assert complete and complete[0]["ok"] is False
