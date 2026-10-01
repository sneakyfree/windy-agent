"""ADR-064 — the desktop lane fetches Eternitas's certificate of record.

Pins the one-authority contract: registration sends a certificate seed and
captures the canonical certificate block; the birth-certificate step adopts
Eternitas's certificate_no (never a locally-minted ``WF-`` number) and saves
Eternitas's signed PDF; offline hatches go to recovery for retry instead of
silently printing their own document.
"""

from __future__ import annotations



class _Resp:
    def __init__(self, status: int, content: bytes = b"", json_data: dict | None = None):
        self.status_code = status
        self.content = content
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


class _Client:
    """Minimal fake httpx client (same pattern as the QR-fetch contract test)."""

    def __init__(self, routes: dict[str, _Resp]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> _Resp:
        self.calls.append(url)
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return _Resp(404)

    def close(self) -> None:
        pass


# ── Registration payload carries the certificate seed ──────────────────


def test_registration_payload_includes_certificate_seed() -> None:
    from windyfly.eternitas.models import RegistrationRequest

    req = RegistrationRequest(
        name="Pip",
        owner_name="Granny Smith",
        model_id="claude-haiku-4-5",
        hatch_machine_id="machine-123",
        hatch_timezone="America/New_York",
        hardware_specs={"cpu": "M1", "os": "macOS"},
    )
    payload = req.to_api_payload()
    seed = payload["certificate"]
    assert seed["owner_name"] == "Granny Smith"
    assert seed["hatch_timezone"] == "America/New_York"
    assert seed["machine_uuid"] == "machine-123"
    assert seed["model_id"] == "claude-haiku-4-5"
    assert seed["hardware_specs"] == {"cpu": "M1", "os": "macOS"}
    assert seed["brain_provider"] == "windyfly"


def test_passport_captures_certificate_block() -> None:
    from windyfly.eternitas.models import EternitasPassport

    data = {
        "passport": "ET26-AAAA-0001",
        "ept_token": "ept-x",
        "certificate": {
            "certificate_no": "ET-DEADBEEF",
            "pdf_url": "/api/v1/certificates/ET26-AAAA-0001/pdf",
        },
    }
    passport = EternitasPassport.from_api_response(data)
    assert passport.certificate["certificate_no"] == "ET-DEADBEEF"

    # Older servers (no certificate key) → empty dict, never None.
    legacy = EternitasPassport.from_api_response({"passport": "ET26-BBBB-0002"})
    assert legacy.certificate == {}


# ── Fetch helpers ───────────────────────────────────────────────────────


# ── Orchestrator step: Eternitas is the authority ───────────────────────
