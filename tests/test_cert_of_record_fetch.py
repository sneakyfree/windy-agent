"""ADR-064 — the desktop lane fetches Eternitas's certificate of record.

Pins the one-authority contract: registration sends a certificate seed and
captures the canonical certificate block; the birth-certificate step adopts
Eternitas's certificate_no (never a locally-minted ``WF-`` number) and saves
Eternitas's signed PDF; offline hatches go to recovery for retry instead of
silently printing their own document.
"""

from __future__ import annotations

import pytest


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


def test_fetch_certificate_pdf_saves_signed_document(tmp_path) -> None:
    from windyfly.birth_certificate import fetch_eternitas_certificate_pdf

    client = _Client({"/pdf": _Resp(200, b"%PDF-1.4 fake-signed-cert")})
    path = fetch_eternitas_certificate_pdf(
        "ET26-AAAA-0001",
        directory=str(tmp_path),
        base_url="https://eternitas.test",
        http_client=client,
    )
    assert path.endswith("birth_certificate_ET26-AAAA-0001.pdf")
    assert (tmp_path / "birth_certificate_ET26-AAAA-0001.pdf").read_bytes().startswith(b"%PDF-")
    assert client.calls == [
        "https://eternitas.test/api/v1/certificates/ET26-AAAA-0001/pdf"
    ]


def test_fetch_certificate_pdf_rejects_non_pdf_and_failures(tmp_path) -> None:
    from windyfly.birth_certificate import fetch_eternitas_certificate_pdf

    # 404 (not yet minted)
    assert fetch_eternitas_certificate_pdf(
        "ET26-X", directory=str(tmp_path),
        base_url="https://eternitas.test", http_client=_Client({}),
    ) == ""
    # 200 but not a PDF body
    assert fetch_eternitas_certificate_pdf(
        "ET26-X", directory=str(tmp_path),
        base_url="https://eternitas.test",
        http_client=_Client({"/pdf": _Resp(200, b"<html>error</html>")}),
    ) == ""


def test_fetch_certificate_json_returns_record_or_empty() -> None:
    from windyfly.birth_certificate import fetch_eternitas_certificate_json

    record = {"certificate_no": "ET-12345678", "agent_name": "Pip"}
    client = _Client({"ET26-AAAA-0001": _Resp(200, json_data=record)})
    assert fetch_eternitas_certificate_json(
        "ET26-AAAA-0001", base_url="https://eternitas.test", http_client=client
    ) == record

    assert fetch_eternitas_certificate_json(
        "ET26-GONE", base_url="https://eternitas.test", http_client=_Client({})
    ) == {}


# ── Orchestrator step: Eternitas is the authority ───────────────────────


@pytest.mark.asyncio
async def test_birth_cert_step_adopts_eternitas_number_and_pdf(
    tmp_path, monkeypatch
) -> None:
    import windyfly.birth_certificate as bc
    from windyfly.hatch_orchestrator import HatchResult, _step_birth_certificate

    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")

    saved = tmp_path / "birth_certificate_ET26-AAAA-0001.pdf"

    def _fake_pdf(passport_id, directory="data", **kw):
        saved.write_bytes(b"%PDF- signed by eternitas")
        return str(saved)

    monkeypatch.setattr(bc, "fetch_eternitas_certificate_pdf", _fake_pdf)

    result = HatchResult(
        agent_name="Pip",
        passport_id="ET26-AAAA-0001",
        eternitas_certificate={"certificate_no": "ET-CAFEF00D"},
    )
    await _step_birth_certificate(result, None)

    # The number is Eternitas's — no locally-minted WF- formula anywhere.
    assert result.certificate_number == "ET-CAFEF00D"
    assert not result.certificate_number.startswith("WF-")
    assert result.birth_certificate_path == str(saved)
    assert result.neural_fingerprint  # display data still generated locally
    assert not any(e.startswith("Birth cert:") for e in result.errors)


@pytest.mark.asyncio
async def test_birth_cert_step_refetches_number_when_missing(
    tmp_path, monkeypatch
) -> None:
    """Retry path: no block from registration → certificate JSON is fetched."""
    import windyfly.birth_certificate as bc
    from windyfly.hatch_orchestrator import HatchResult, _step_birth_certificate

    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setattr(
        bc, "fetch_eternitas_certificate_json",
        lambda passport_id, **kw: {"certificate_no": "ET-0BADF00D"},
    )
    monkeypatch.setattr(
        bc, "fetch_eternitas_certificate_pdf",
        lambda passport_id, directory="data", **kw: str(tmp_path / "cert.pdf"),
    )

    result = HatchResult(agent_name="Pip", passport_id="ET26-AAAA-0002")
    await _step_birth_certificate(result, None)
    assert result.certificate_number == "ET-0BADF00D"


@pytest.mark.asyncio
async def test_birth_cert_step_offline_goes_pending_not_local_print(
    monkeypatch,
) -> None:
    """Eternitas configured but unreachable → honest pending + retry, and
    NO locally-rendered PDF masquerading as the certificate of record."""
    import windyfly.birth_certificate as bc
    from windyfly.hatch_orchestrator import HatchResult, _step_birth_certificate

    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setattr(bc, "fetch_eternitas_certificate_json", lambda *a, **kw: {})
    monkeypatch.setattr(bc, "fetch_eternitas_certificate_pdf", lambda *a, **kw: "")

    result = HatchResult(agent_name="Pip", passport_id="ET26-AAAA-0003")
    await _step_birth_certificate(result, None)

    assert result.birth_certificate_path == ""
    assert result.certificate_number == ""
    assert any("pending" in e for e in result.errors)


@pytest.mark.asyncio
async def test_birth_cert_step_mock_mode_saves_labeled_preview(
    tmp_path, monkeypatch
) -> None:
    """No Eternitas configured (dev/mock lane) → local preview, no recovery loop."""
    from windyfly.hatch_orchestrator import HatchResult, _step_birth_certificate

    monkeypatch.delenv("ETERNITAS_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    result = HatchResult(
        agent_name="Pip",
        passport_id="ET-L00001",
        eternitas_certificate={"certificate_no": "ET-MOCK1234"},
    )
    await _step_birth_certificate(result, None)
    assert result.certificate_number == "ET-MOCK1234"
    assert result.birth_certificate_path  # preview saved for dev UX
    assert not any("pending" in e for e in result.errors)


def test_save_recovery_tracks_pending_certificate(tmp_path, monkeypatch) -> None:
    import json

    import windyfly.hatch_orchestrator as ho
    from windyfly.hatch_orchestrator import HatchResult, _save_recovery

    recovery = tmp_path / "provision_recovery.json"
    monkeypatch.setattr(ho, "_RECOVERY_PATH", recovery)

    result = HatchResult(agent_name="Pip", passport_id="ET26-AAAA-0004")
    result.errors.append("Birth cert: Eternitas certificate pending (fetch failed; will retry)")
    _save_recovery(result)

    data = json.loads(recovery.read_text())
    assert "birth_certificate" in data["failed_steps"]
