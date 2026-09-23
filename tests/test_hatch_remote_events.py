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


@pytest.fixture
def scratch_env(monkeypatch):
    """Swap ``os.environ`` for a copy so a test that WRITES env vars
    (the whole point of the ones below) cannot leak into the session.

    ``monkeypatch.delenv`` only records keys that already existed, so a
    key created by the code under test would otherwise survive teardown.
    """
    import os as _os

    env = dict(_os.environ)
    monkeypatch.setattr(_os, "environ", env)
    return env


def test_apply_broker_token_pins_to_the_provider_argument(scratch_env) -> None:
    """The /hatch/remote `provider` field must pin the token to ONE env var.

    windy-pro tells us which provider the broker minted the token for.
    Copying that token into the other seven providers' env vars is not
    "permissive", it is wrong — the string is dead everywhere else and
    only makes the wrong client try it.
    """
    from windyfly.hatch_remote import PROVIDER_TO_ENV, _apply_broker_token

    for key in set(PROVIDER_TO_ENV.values()) | {"WINDY_BROKER_PROVIDER"}:
        scratch_env.pop(key, None)

    env_var = _apply_broker_token("bk_live_xyz", "openai")

    assert env_var == "OPENAI_API_KEY"
    assert scratch_env["OPENAI_API_KEY"] == "bk_live_xyz"
    for key in set(PROVIDER_TO_ENV.values()) - {"OPENAI_API_KEY"}:
        assert scratch_env.get(key, "") == "", f"{key} must not receive an openai token"


def test_apply_broker_token_argument_beats_env_override(scratch_env) -> None:
    from windyfly.hatch_remote import PROVIDER_TO_ENV, _apply_broker_token

    for key in set(PROVIDER_TO_ENV.values()):
        scratch_env.pop(key, None)
    scratch_env["WINDY_BROKER_PROVIDER"] = "openai"

    assert _apply_broker_token("bk_live_xyz", "anthropic") == "ANTHROPIC_API_KEY"
    assert scratch_env.get("OPENAI_API_KEY", "") == ""


def test_apply_broker_token_unknown_provider_falls_back_to_all(scratch_env) -> None:
    """An unrecognised provider is the documented fallback, not a crash."""
    from windyfly.hatch_remote import PROVIDER_TO_ENV, _apply_broker_token

    for key in set(PROVIDER_TO_ENV.values()) | {"WINDY_BROKER_PROVIDER"}:
        scratch_env.pop(key, None)

    assert _apply_broker_token("bk_live_xyz", "not-a-provider") == "*"
    assert scratch_env["ANTHROPIC_API_KEY"] == "bk_live_xyz"


def test_main_accepts_the_gateway_argv_contract(monkeypatch) -> None:
    """`main()` must parse every flag the Bun gateway spawns it with.

    The gateway builds this argv in gateway/src/hatch-remote.ts; if the
    two drift, the handoff dies at process start with SystemExit(2).
    """
    from windyfly import hatch_remote

    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(hatch_remote, "run", fake_run)

    rc = hatch_remote.main([
        "--agent-name", "Nora's Agent",
        "--windy-identity-id", "wi_123",
        "--passport-number", "ET26-ABC-DEF",
        "--broker-token", "bk_live_abcdefghijkl",
        "--owner-email", "nora@example.com",
        "--owner-phone", "",
        "--owner-name", "Nora",
        "--bot-identity-id", "wi_bot_456",
        "--provider", "anthropic",
        "--model", "claude-3-5-sonnet-latest",
    ])

    assert rc == 0
    assert captured["bot_identity_id"] == "wi_bot_456"
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-3-5-sonnet-latest"
    # A phone-less owner is a legitimate hatch, not an error.
    assert captured["owner_phone"] == ""


def test_run_seeds_bot_identity_id_into_the_environment(monkeypatch, scratch_env) -> None:
    """BOT_IDENTITY_ID is the ONLY carrier of the bot's Pro identity.

    The orchestrator takes no `bot_identity_id` argument, so the remote
    door hands it down through the environment exactly like the passport
    (ETERNITAS_PASSPORT) — bot credential minting reads it from there.
    """
    from windyfly import hatch_remote

    for key in ("BOT_IDENTITY_ID", "WINDY_BROKER_MODEL", "DEFAULT_MODEL", "ANTHROPIC_API_KEY"):
        scratch_env.pop(key, None)

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        hatch_remote, "_emit_json",
        lambda name, data: seen.append((name, data)),
    )
    # Stop before the ceremony/orchestrator — this test is about the
    # environment seeding that happens first.
    monkeypatch.setattr(
        hatch_remote, "_apply_broker_token",
        lambda token, provider="": f"seeded:{provider}",
    )

    import windyfly.hatching as hatching

    def stop(*args, **kwargs):
        raise RuntimeError("stop after seeding")

    monkeypatch.setattr(hatching, "play_hatching", stop)

    with pytest.raises(RuntimeError):
        hatch_remote.run(
            agent_name="Nora's Agent",
            windy_identity_id="wi_123",
            passport_number="ET26-ABC-DEF",
            broker_token="bk_live_abcdefghijkl",
            owner_email="nora@example.com",
            owner_phone="",
            owner_name="Nora",
            bot_identity_id="wi_bot_456",
            provider="anthropic",
            model="claude-3-5-sonnet-latest",
            animate=False,
        )

    assert scratch_env["BOT_IDENTITY_ID"] == "wi_bot_456"
    assert scratch_env["WINDY_BROKER_MODEL"] == "claude-3-5-sonnet-latest"
    assert scratch_env["DEFAULT_MODEL"] == "claude-3-5-sonnet-latest"

    starting = [d for n, d in seen if n == "hatch.starting"]
    assert starting, "hatch.starting must be emitted"
    assert starting[0]["bot_identity_id"] == "wi_bot_456"
    assert starting[0]["broker_provider"] == "anthropic"
    assert starting[0]["broker_model"] == "claude-3-5-sonnet-latest"


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


# ── audit §2e #5: the remote door never adopts the HOST agent's identity ──

_HOST_IDENTITY = {
    "ETERNITAS_PASSPORT": "ET26-HOST-0001",
    "ETERNITAS_PASSPORT_TOKEN": "host.ept.token",
    "ETERNITAS_OPERATOR_JWT": "host.operator.jwt",
    "WINDY_HUB_JWT": "host.hub.jwt",
    "WINDY_ENV_FILE": "/home/host/.windy/host.env",
    "WINDY_CREDENTIALS_FILE": "/home/host/.windy/credentials.json",
}


def _run_until_ceremony(monkeypatch, scratch_env, passport_number: str) -> tuple[int | None, list]:
    from windyfly import hatch_remote
    import windyfly.hatching as hatching

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(hatch_remote, "_emit_json", lambda n, d: seen.append((n, d)))
    monkeypatch.setattr(hatch_remote, "_apply_broker_token", lambda t, p="": "seeded")

    def stop(*a, **k):
        raise RuntimeError("stop after seeding")

    monkeypatch.setattr(hatching, "play_hatching", stop)
    try:
        rc = hatch_remote.run(
            agent_name="Nora's Agent", windy_identity_id="wi_123",
            passport_number=passport_number, broker_token="bk_live_abcdefghijkl",
            owner_email="nora@example.com", owner_phone="", owner_name="Nora",
            animate=False,
        )
    except RuntimeError:
        rc = None
    return rc, seen


@pytest.mark.parametrize("passport", ["", "   "])
def test_run_refuses_an_empty_passport(monkeypatch, scratch_env, passport) -> None:
    scratch_env.update(_HOST_IDENTITY)
    rc, seen = _run_until_ceremony(monkeypatch, scratch_env, passport)
    assert rc is not None and rc != 0, "an empty passport must not reach the ceremony"
    assert any(n == "hatch.error" for n, _ in seen)
    assert scratch_env.get("ETERNITAS_PASSPORT") != "ET26-HOST-0001"


def test_run_drops_the_host_identity_before_seeding(monkeypatch, scratch_env) -> None:
    scratch_env.update(_HOST_IDENTITY)
    rc, _ = _run_until_ceremony(monkeypatch, scratch_env, "ET26-ABC-DEF")
    assert rc is None  # reached the ceremony
    assert scratch_env["ETERNITAS_PASSPORT"] == "ET26-ABC-DEF"
    for key in _HOST_IDENTITY:
        if key != "ETERNITAS_PASSPORT":
            assert key not in scratch_env, f"{key} leaked from the host"


def test_main_does_not_default_the_passport_from_the_host(monkeypatch, scratch_env) -> None:
    from windyfly import hatch_remote

    scratch_env.update(_HOST_IDENTITY)
    captured: dict[str, object] = {}
    monkeypatch.setattr(hatch_remote, "run", lambda **kw: captured.update(kw) or 0)
    hatch_remote.main([
        "--windy-identity-id", "wi_123", "--broker-token", "bk_live_abcdefghijkl",
        "--owner-email", "nora@example.com", "--owner-name", "Nora",
    ])
    assert captured["passport_number"] == ""
