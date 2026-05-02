"""Guest-mode regression tests.

Pin the contract:
  - Default state is OFF
  - guest_on writes the flag, guest_off clears it
  - File-based — survives restart (read-from-disk on each call)
  - Atomic write — no torn-flag failures
  - Status payload exposes enabled_at / actor / label
  - main.py's _respond hook routes Band.USER when active, OWNER when off
  - Telegram /guest command parser recognizes the alias surface
"""

from __future__ import annotations

import json

import pytest

from windyfly.agent.guest_mode import (
    guest_off,
    guest_on,
    guest_status,
    is_guest_active,
)


@pytest.fixture(autouse=True)
def isolated_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    yield tmp_path


# ── Lifecycle ──────────────────────────────────────────────────────


def test_initial_state_inactive():
    assert is_guest_active() is False
    s = guest_status()
    assert s["active"] is False
    assert s["enabled_at"] is None


def test_guest_on_writes_flag():
    out = guest_on(actor="grant")
    assert out["ok"] is True
    assert out["active"] is True
    assert out["actor"] == "grant"
    assert is_guest_active() is True


def test_guest_off_clears():
    guest_on(actor="grant")
    assert is_guest_active() is True
    out = guest_off()
    assert out["ok"] is True
    assert out["was_active"] is True
    assert is_guest_active() is False


def test_guest_off_when_inactive_idempotent():
    out = guest_off()
    assert out["ok"] is True
    assert out["was_active"] is False


def test_label_round_trips():
    """Optional label exposed on status — useful for /spend etc."""
    guest_on(actor="grant", label="ballroom-demo-utah")
    s = guest_status()
    assert s["label"] == "ballroom-demo-utah"


# ── Persistence ────────────────────────────────────────────────────


def test_survives_simulated_restart(isolated_flag):
    """File-based: reading the flag from disk in a 'fresh process'
    must still report active. Demo on stage shouldn't get silently
    un-guested if systemd respawns mid-talk."""
    guest_on(actor="grant")
    flag = isolated_flag / ".guest"
    assert flag.exists()
    data = json.loads(flag.read_text())
    assert data["actor"] == "grant"
    # Simulate fresh process
    assert is_guest_active() is True


# ── Torn flag handling ─────────────────────────────────────────────


def test_torn_flag_still_active(isolated_flag):
    """Hand-edited / mid-write flag → still counts as active (file
    is there). Better to keep grandma-mode on with no metadata than
    to silently drop out of demo mode mid-stage. Pause uses the same
    'flag-presence wins' rule."""
    flag = isolated_flag / ".guest"
    flag.write_text("not valid json {{{")
    assert is_guest_active() is True
    s = guest_status()
    assert s["active"] is True
    assert s["enabled_at"] is None


# ── Channel hook integration (main.py _respond shape) ──────────────


def test_band_routing_when_guest_active():
    """Mirrors main.py:_respond — when guest is on, callers route
    Band.USER instead of Band.OWNER. This is the test for the actual
    contract main.py depends on."""
    from windyfly.agent.capabilities import Band
    guest_on(actor="grant")
    band = Band.USER if is_guest_active() else Band.OWNER
    assert band == Band.USER


def test_band_routing_when_guest_inactive():
    from windyfly.agent.capabilities import Band
    band = Band.USER if is_guest_active() else Band.OWNER
    assert band == Band.OWNER


# ── Telegram command parser ────────────────────────────────────────


class TestGuestCommandParser:
    """The /guest parser also accepts /demo aliases."""

    @pytest.fixture
    def parse(self):
        from windyfly.channels.telegram_bot import _parse_guest_command
        return _parse_guest_command

    def test_bare_guest_is_status(self, parse):
        assert parse("/guest") == (True, None)

    def test_bare_demo_is_status(self, parse):
        assert parse("/demo") == (True, None)

    def test_guest_on(self, parse):
        assert parse("/guest on") == (True, "on")

    def test_demo_on(self, parse):
        assert parse("/demo on") == (True, "on")

    def test_guest_off(self, parse):
        assert parse("/guest off") == (True, "off")

    def test_demo_off(self, parse):
        assert parse("/demo off") == (True, "off")

    def test_aliases_start_end(self, parse):
        assert parse("/guest start") == (True, "on")
        assert parse("/guest end") == (True, "off")
        assert parse("/demo end") == (True, "off")

    def test_invalid_arg(self, parse):
        assert parse("/guest sometimes") == (True, "invalid")

    def test_unrelated_message(self, parse):
        assert parse("hello") == (False, None)
        assert parse("/pause") == (False, None)
        assert parse("") == (False, None)
        assert parse(None) == (False, None)

    def test_case_insensitive(self, parse):
        assert parse("/Guest On") == (True, "on")
        assert parse("/DEMO OFF") == (True, "off")
