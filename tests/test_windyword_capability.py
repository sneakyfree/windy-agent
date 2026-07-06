"""Windy Word app-control capabilities — the grandma-turns-the-dials bridge.

Verifies the handlers build the right requests to the local control surface,
parse responses, resolve friendly pack names, clamp volumes, and — critically —
fail SOFT when the app is closed so the agent tells the user to open it instead
of throwing mid-turn.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from windyfly.agent.capabilities import windyword as ww
from windyfly.agent.capabilities.registry import CapabilityRegistry


def _resp(status=200, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body if json_body is not None else {}
    r.text = text
    return r


def test_status_summarizes_sound_and_widget():
    def fake_get(url, timeout=0):
        if url.endswith("/sound-effects/state"):
            return _resp(json_body={"ok": True, "state": {
                "mode": "pack", "activePackName": "Wizard",
                "hookPoints": {"start": {"enabled": True, "volume": 70}}}})
        return _resp(json_body={"ok": True, "state": {"widgetVisible": True}})

    with patch.object(httpx, "get", side_effect=fake_get):
        out = ww._status()
    assert out["ok"] is True
    assert out["sounds"]["active_pack"] == "Wizard"
    assert out["sounds"]["hooks"]["start"] == {"enabled": True, "volume": 70}
    assert "master_volume" not in out["sounds"]  # omitted when absent
    assert out["widget"] == {"widgetVisible": True}


def test_set_master_volume_clamps_and_posts():
    seen = {}

    def fake_post(url, json=None, timeout=0):
        seen["url"] = url
        seen["body"] = json
        return _resp(json_body={"ok": True, "masterVolume": json["volume"]})

    with patch.object(httpx, "post", side_effect=fake_post):
        out = ww._set_master_volume(volume=150)  # over max
    assert seen["url"].endswith("/sound-effects/master-volume")
    assert seen["body"] == {"volume": 100}  # clamped
    assert out["ok"] and "100%" in out["message"]


def test_set_sound_requires_a_change():
    out = ww._set_sound(hook="start")  # no enabled/volume
    assert out["ok"] is False and "enabled" in out["error"]


def test_set_sound_pack_resolves_friendly_name():
    def fake_get(url, timeout=0):
        return _resp(json_body={"ok": True, "packs": [
            {"id": "_silent", "name": "🔇 Silent"},
            {"id": "wizard", "name": "🧙 Wizard"}]})
    posted = {}

    def fake_post(url, json=None, timeout=0):
        posted["body"] = json
        return _resp(json_body={"ok": True, "activePackId": json["packId"]})

    with patch.object(httpx, "get", side_effect=fake_get), \
         patch.object(httpx, "post", side_effect=fake_post):
        out = ww._set_sound_pack(pack_id="silent")  # friendly, lowercase
    assert posted["body"] == {"packId": "_silent"}
    assert out["ok"]


def test_set_sound_pack_unknown_lists_options():
    def fake_get(url, timeout=0):
        return _resp(json_body={"ok": True, "packs": [{"id": "wizard", "name": "Wizard"}]})
    with patch.object(httpx, "get", side_effect=fake_get):
        out = ww._set_sound_pack(pack_id="nonsense")
    assert out["ok"] is False and "Available" in out["error"]


def test_app_down_fails_soft_not_raises():
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=boom), \
         patch.object(httpx, "post", side_effect=boom):
        for call in (
            lambda: ww._status(),
            lambda: ww._set_master_volume(volume=50),
            lambda: ww._set_setting(path="appearance.theme", value="dark"),
        ):
            out = call()
            assert out["ok"] is False
            assert "open windy word" in out["error"].lower()


def test_set_setting_passes_structured_4xx_through():
    def fake_post(url, json=None, timeout=0):
        return _resp(status=422, json_body={"path": json["path"],
                                            "error": "unknown setting"})
    with patch.object(httpx, "post", side_effect=fake_post):
        out = ww._set_setting(path="bad.path", value=1)
    assert out["ok"] is False and out["error"] == "unknown setting"


def test_registration_adds_six_band_user_capabilities(monkeypatch):
    monkeypatch.delenv("WINDY_WORD_CONTROL", raising=False)
    reg = CapabilityRegistry()
    ww.register_windyword_capabilities(reg)
    ids = [c.id for c in reg.all()] if hasattr(reg, "all") else \
        [reg.get(i).id for i in (
            "windyword.status", "windyword.set_master_volume",
            "windyword.set_sound", "windyword.set_sound_pack",
            "windyword.list_settings", "windyword.set_setting")]
    for cap in ("windyword.status", "windyword.set_master_volume",
                "windyword.set_sound", "windyword.set_sound_pack",
                "windyword.list_settings", "windyword.set_setting"):
        assert reg.get(cap) is not None, f"{cap} not registered"


def test_registration_can_be_disabled(monkeypatch):
    monkeypatch.setenv("WINDY_WORD_CONTROL", "0")
    reg = CapabilityRegistry()
    ww.register_windyword_capabilities(reg)
    assert reg.get("windyword.status") is None
