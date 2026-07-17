"""Wave 8: SSE event ordering + JSON render mode for the remote hatch.

These tests pin the event stream emitted by the hatch orchestrator so
the gateway can rely on a stable order when relaying SSE frames to
windy-pro's Electron UI. If you add a new stage, update ``expected``
below AND ``hatch_remote.EVENT_ORDER``.
"""

from __future__ import annotations

from typing import Any

import pytest

from windyfly.hatch_orchestrator import orchestrate_hatch
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip any ecosystem credentials so the hatch uses only mocks."""
    for key in (
        "ETERNITAS_URL", "ETERNITAS_API_URL", "ETERNITAS_PASSPORT",
        "WINDY_JWT", "WINDY_IDENTITY_ID", "WINDY_CLOUD_URL",
        "SYNAPSE_REGISTRATION_SECRET",
        "TWILIO_ACCOUNT_SID", "TWILIO_PHONE_NUMBER",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_API_URL",
        "OWNER_PHONE", "OWNER_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)


async def test_event_stream_order_matches_contract(db) -> None:
    """The orchestrator must emit events in the contract-documented order.

    The SSE consumer (Electron) drives spinner→checkmark transitions
    off the *.provisioning → *.provisioned pairs, so reordering any
    of them silently would break the UI.
    """
    events: list[tuple[str, dict[str, Any]]] = []

    def on_event(name: str, data: dict[str, Any]) -> None:
        events.append((name, dict(data)))

    await orchestrate_hatch(
        agent_name="order-fly",
        owner_name="Nora",
        db=db,
        on_event=on_event,
    )

    names = [e[0] for e in events]

    # The Eternitas pair must bracket everything else.
    assert names.index("eternitas.registering") == 0
    assert names.index("eternitas.registered") > names.index("eternitas.registering")

    # Each product-starting event must fire before its -done/-provisioned counterpart.
    pairs = [
        ("mail.provisioning",              "mail.provisioned"),
        ("chat.provisioning",              "chat.provisioned"),
        ("phone.assigning",                "phone.assigned"),
        ("cloud.provisioning",             "cloud.provisioned"),
        ("birth_certificate.generating",   "birth_certificate.ready"),
    ]
    for start, done in pairs:
        assert start in names, f"missing start event: {start}"
        assert done in names, f"missing done event: {done}"
        assert names.index(start) < names.index(done), \
            f"{start} must fire before {done}"

    # hatch.complete must always be the tail event.
    assert names[-1] == "hatch.complete", f"last event was {names[-1]}"


async def test_birth_certificate_ready_payload_includes_rich(db, monkeypatch) -> None:
    """birth_certificate.ready must include the rich payload (SVG, fields).

    The Electron consumer expects a single round-trip — anything it
    needs to render the cert must be in this event. We stub the
    remote-asset fetch to keep the test network-free.
    """
    # Make fetch_eternitas_assets a no-op so we exercise the local SVG
    # fallback path without pretending we have Eternitas running.
    import windyfly.birth_certificate as bc
    monkeypatch.setattr(bc, "fetch_eternitas_assets", lambda *a, **kw: {})

    events: list[tuple[str, dict[str, Any]]] = []
    await orchestrate_hatch(
        agent_name="rich-fly",
        db=db,
        on_event=lambda n, d: events.append((n, d)),
    )

    ready = [e for e in events if e[0] == "birth_certificate.ready"]
    assert len(ready) == 1
    payload = ready[0][1]
    assert "rich" in payload, "missing rich payload in ready event"
    rich = payload["rich"]
    assert rich["agent_name"] == "rich-fly"
    # ADR-064: Eternitas's certificate number (ET-…), not the retired WF- one.
    assert rich["certificate_number"].startswith("ET-")
    assert rich["neural_art_svg"].startswith("<svg")
    # Eternitas only ships the QR endpoint — the neural-art SVG is
    # generated locally and must never carry a *_remote field.
    assert "neural_art_svg_remote" not in rich
    assert "passport_qr_png_b64" not in rich


async def test_every_phase_event_carries_ok_flag(db) -> None:
    """Wave 11 bug #10/11 contract pin: every *.provisioned / *.ready /
    *.registered / *.assigned / hatch.complete event MUST carry an `ok`
    bool so consumers can gate their green-tick on reality, not on the
    event name."""
    events: list[tuple[str, dict]] = []
    await orchestrate_hatch(
        agent_name="ok-flag-fly",
        db=db,
        on_event=lambda n, d: events.append((n, d)),
    )
    success_terminals = {
        "eternitas.registered",
        "mail.provisioned",
        "chat.provisioned",
        "phone.assigned",
        "cloud.provisioned",
        "birth_certificate.ready",
        "hatch.complete",
    }
    for name, data in events:
        if name in success_terminals:
            assert "ok" in data, f"{name} must carry an `ok` flag"
            assert isinstance(data["ok"], bool), f"{name}.data.ok must be bool"


async def test_callback_exception_does_not_break_hatch(db) -> None:
    """A buggy consumer must never block provisioning.

    Rule: orchestrator wraps every callback invocation in try/except.
    This test proves it by raising from every single event and still
    expecting a successful hatch.
    """
    def bomb(name: str, data: dict) -> None:
        raise RuntimeError(f"boom at {name}")

    result = await orchestrate_hatch(
        agent_name="bomb-fly",
        db=db,
        on_event=bomb,
    )
    # The Eternitas mock always succeeds → we should still have a passport.
    assert result.passport_id.startswith("ET-L")


def test_hatch_remote_json_emit_is_one_line() -> None:
    """Each event must fit on a single line so the Bun gateway can
    split the subprocess's stdout by ``\\n`` without reassembly."""
    import json
    import io
    import sys

    from windyfly.hatch_remote import _emit_json

    buf = io.StringIO()
    orig = sys.stdout
    sys.stdout = buf
    try:
        _emit_json("test.event", {"nested": {"a": 1, "b": [1, 2, 3]}})
    finally:
        sys.stdout = orig

    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == {"event": "test.event", "data": {"nested": {"a": 1, "b": [1, 2, 3]}}}


def test_apply_broker_token_respects_preferred_provider(monkeypatch) -> None:
    """WINDY_BROKER_PROVIDER should pin the broker credential to a single env var."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WINDY_BROKER_PROVIDER", "anthropic")

    from windyfly.hatch_remote import _apply_broker_token
    env_var = _apply_broker_token("wk_broker_xyz")

    import os as _os
    assert env_var == "ANTHROPIC_API_KEY"
    assert _os.environ["ANTHROPIC_API_KEY"] == "wk_broker_xyz"
    # Other providers must NOT be populated when a preference is set.
    assert _os.environ.get("OPENAI_API_KEY", "") == ""


def test_apply_broker_token_without_preference_populates_all(monkeypatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("WINDY_BROKER_PROVIDER", raising=False)

    from windyfly.hatch_remote import _apply_broker_token
    env_var = _apply_broker_token("wk_broker_xyz")

    import os as _os
    assert env_var == "*"
    assert _os.environ["OPENAI_API_KEY"] == "wk_broker_xyz"
    assert _os.environ["ANTHROPIC_API_KEY"] == "wk_broker_xyz"


def test_fetch_eternitas_assets_uses_certificates_qr_endpoint(monkeypatch) -> None:
    """Contract pin for Eternitas: the QR endpoint is
    /api/v1/certificates/{passport}/qr, PNG by default.

    We also assert that we do NOT call any fingerprint.svg endpoint —
    Eternitas doesn't ship one, and the neural mandala is rendered
    locally.
    """
    from windyfly.birth_certificate import fetch_eternitas_assets

    class _Resp:
        def __init__(self, status: int, content: bytes = b""):
            self.status_code = status
            self.content = content

    calls: list[str] = []

    class _Client:
        def get(self, url: str):
            calls.append(url)
            if url.endswith("/qr"):
                # Minimal 1x1 PNG magic bytes, good enough for base64.
                return _Resp(200, b"\x89PNG\r\n\x1a\n")
            return _Resp(404)

        def close(self) -> None:
            pass

    out = fetch_eternitas_assets(
        "ET26-ABC-DEF",
        base_url="https://eternitas.test",
        http_client=_Client(),
    )

    assert calls == ["https://eternitas.test/api/v1/certificates/ET26-ABC-DEF/qr"]
    # Contract: no fingerprint.svg fetch.
    assert not any("fingerprint" in c for c in calls)
    assert "qr_png_b64" in out
    # And we must never carry a remote fingerprint field.
    assert "fingerprint_svg" not in out


def test_play_hatching_json_emits_all_stages() -> None:
    """--render-mode=json must emit every ceremony stage as an event."""
    from windyfly.hatching import play_hatching

    events: list[tuple[str, dict]] = []
    play_hatching(
        animate=False,
        render_mode="json",
        on_event=lambda n, d: events.append((n, d)),
    )

    stage_events = [e for e in events if e[0] == "ceremony.stage"]
    assert len(stage_events) == 4, f"expected 4 ceremony stages, got {len(stage_events)}"
    # Stages must arrive in-order with increasing indices.
    for expected_index, (_, data) in enumerate(stage_events):
        assert data["index"] == expected_index
        assert data["total"] == 4
    assert events[-1][0] == "ceremony.complete"
