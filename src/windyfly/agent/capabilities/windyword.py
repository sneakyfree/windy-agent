"""Windy Word desktop-app control capabilities.

The grandma promise (project_windy_word_agent_native_vision): a normie tells
her Windy Fly agent "turn the sounds down" or "make the tornado bigger" and the
agent reaches over and turns the dial on her Windy Word app — no menus, no
settings screens.

Windy Word serves a local HTTP control surface on 127.0.0.1:18765 (the same one
windy-word-mcp wraps). Because a grandma's Fly runs on the same machine as her
Windy Word app, the agent can drive it directly. These capabilities are the
Fly-native front door to that surface: register on the Capability Plane so the
LLM can pick them by description, band-gated to USER (the owner adjusting her
own app), all reversible.

If the app isn't running the calls fail soft with a plain-English "open Windy
Word first" so the agent can relay it instead of throwing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

_HOOKS = ("start", "during", "stop", "process", "warning", "paste")


def _base_url() -> str:
    return os.environ.get("WINDY_WORD_URL", "http://127.0.0.1:18765").rstrip("/")


def _app_down() -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            "I can't reach your Windy Word app — it doesn't look like it's "
            "open. Ask the user to open Windy Word, then try again."
        ),
    }


def _get(path: str) -> dict[str, Any]:
    import httpx

    try:
        r = httpx.get(f"{_base_url()}{path}", timeout=6.0)
        if r.status_code >= 400:
            return {"ok": False, "error": f"Windy Word returned {r.status_code}"}
        try:
            return r.json()
        except Exception:
            return {"ok": False, "error": r.text[:200] or "no response body"}
    except httpx.ConnectError:
        return _app_down()
    except Exception as e:  # never crash the turn over app control
        return {"ok": False, "error": f"Windy Word control failed: {e}"}


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    import httpx

    try:
        r = httpx.post(f"{_base_url()}{path}", json=body, timeout=6.0)
        if r.status_code >= 400:
            # The surface returns structured JSON on 4xx — pass it through.
            try:
                return {"ok": False, **r.json()}
            except Exception:
                return {"ok": False, "error": f"Windy Word returned {r.status_code}: {r.text[:150]}"}
        try:
            return r.json()
        except Exception:
            return {"ok": True}
    except httpx.ConnectError:
        return _app_down()
    except Exception as e:
        return {"ok": False, "error": f"Windy Word control failed: {e}"}


# ─── handlers ─────────────────────────────────────────────────────────


def _status() -> dict[str, Any]:
    sfx = _get("/sound-effects/state")
    if not sfx.get("ok"):
        return sfx  # app down / error — surface it as-is
    widget = _get("/widget/state")
    state = sfx.get("state", {})
    hooks = {
        name: {
            "enabled": h.get("enabled"),
            "volume": h.get("volume"),
        }
        for name, h in (state.get("hookPoints") or {}).items()
    }
    sounds: dict[str, Any] = {
        "mode": state.get("mode"),
        "active_pack": state.get("activePackName") or state.get("activePackId"),
        "hooks": hooks,
    }
    # The state endpoint doesn't echo master volume (there's no GET for it);
    # only include it when present so we don't report a misleading null.
    if state.get("masterVolume") is not None:
        sounds["master_volume"] = state.get("masterVolume")
    return {
        "ok": True,
        "sounds": sounds,
        "widget": widget.get("state", {}) if widget.get("ok") else None,
    }


def _set_master_volume(*, volume: int) -> dict[str, Any]:
    volume = max(0, min(100, int(volume)))
    out = _post("/sound-effects/master-volume", {"volume": volume})
    if out.get("ok"):
        out["message"] = f"Master sound volume set to {volume}%."
    return out


def _set_sound(*, hook: str, enabled: bool | None = None,
               volume: int | None = None) -> dict[str, Any]:
    if hook not in _HOOKS:
        return {"ok": False, "error": f"hook must be one of {', '.join(_HOOKS)}"}
    body: dict[str, Any] = {"hook": hook}
    if enabled is not None:
        body["enabled"] = bool(enabled)
    if volume is not None:
        body["volume"] = max(0, min(100, int(volume)))
    if len(body) == 1:
        return {"ok": False, "error": "Pass enabled and/or volume to change."}
    return _post("/sound-effects/hook", body)


def _set_sound_pack(*, pack_id: str) -> dict[str, Any]:
    # Accept a friendly name or an id; resolve names against the catalog.
    target = pack_id
    packs = _get("/sound-effects/packs")
    if packs.get("ok"):
        options = packs.get("packs", [])
        ids = {p.get("id") for p in options}
        if target not in ids:
            low = target.strip().lower()
            for p in options:
                name = (p.get("name") or "").lower()
                if low in name or low == (p.get("id") or "").lower():
                    target = p.get("id")
                    break
            else:
                names = ", ".join(
                    f"{p.get('name')} ({p.get('id')})" for p in options[:12]
                )
                return {"ok": False,
                        "error": f"No sound pack matching '{pack_id}'. Available: {names}"}
    return _post("/sound-effects/active-pack", {"packId": target})


def _list_settings() -> dict[str, Any]:
    cat = _get("/settings/catalog")
    if not cat.get("ok") and "settings" not in cat:
        return cat if cat.get("error") else {"ok": False, "error": "no catalog"}
    settings = cat.get("settings", cat if isinstance(cat, list) else [])
    return {
        "ok": True,
        "settings": [
            {"path": s.get("path"), "type": s.get("type"),
             "description": s.get("description"), "enum": s.get("enum"),
             "default": s.get("default")}
            for s in settings
        ],
    }


def _set_setting(*, path: str, value: Any) -> dict[str, Any]:
    return _post("/settings/set", {"path": path, "value": value})


# ─── registration ─────────────────────────────────────────────────────


def register_windyword_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register windyword.* capabilities.

    Always registered (the app comes and goes; the handlers fail soft when
    it's closed so the agent can tell the user to open it), unless explicitly
    disabled with WINDY_WORD_CONTROL=0.
    """
    if os.environ.get("WINDY_WORD_CONTROL", "1") == "0":
        logger.info("windyword.* capabilities disabled (WINDY_WORD_CONTROL=0)")
        return

    registry.register(Capability(
        id="windyword.status",
        name="Windy Word status",
        description=(
            "Read the current state of the user's Windy Word desktop app: "
            "sound mode, active sound pack, master volume, per-stage sound "
            "hooks, and the on-screen tornado widget. Use this before "
            "changing a setting so you can tell the user what it is now."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: _status(),
        tier=Tier.READ_EXTERNAL,
    ))

    registry.register(Capability(
        id="windyword.set_master_volume",
        name="Set Windy Word master volume",
        description=(
            "Set the master sound-effects volume of the user's Windy Word app "
            "(0-100). Use this for 'turn the sounds up/down', 'louder', "
            "'quieter'. 0 mutes everything."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "volume": {"type": "integer", "minimum": 0, "maximum": 100,
                           "description": "Master volume 0-100."},
            },
            "required": ["volume"],
        },
        handler=lambda **kw: _set_master_volume(**kw),
        tier=Tier.WRITE_LOCAL_SAFE,
    ))

    registry.register(Capability(
        id="windyword.set_sound",
        name="Configure a Windy Word sound stage",
        description=(
            "Turn a single Windy Word sound stage on/off or set its volume. "
            "Stages: start (recording begins), during (while recording), stop "
            "(recording ends), process (transcribing), warning (near session "
            "limit), paste (transcript pasted). Use for 'turn off the start "
            "sound' or 'make the stop sound quieter'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "hook": {"type": "string", "enum": list(_HOOKS)},
                "enabled": {"type": "boolean",
                            "description": "true = on, false = off."},
                "volume": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["hook"],
        },
        handler=lambda **kw: _set_sound(**kw),
        tier=Tier.WRITE_LOCAL_SAFE,
    ))

    registry.register(Capability(
        id="windyword.set_sound_pack",
        name="Switch Windy Word sound pack",
        description=(
            "Switch the user's Windy Word sound pack by name or id. Use for "
            "'change the sounds', 'use the silent pack', 'mute all sounds' "
            "(pack '🔇 Silent'). Accepts a friendly name; call "
            "windyword.status first if unsure of the options."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pack_id": {"type": "string",
                            "description": "Sound pack name or id (e.g. 'Silent')."},
            },
            "required": ["pack_id"],
        },
        handler=lambda **kw: _set_sound_pack(**kw),
        tier=Tier.WRITE_LOCAL_SAFE,
    ))

    registry.register(Capability(
        id="windyword.list_settings",
        name="List Windy Word settings",
        description=(
            "List the tunable settings of the user's Windy Word app (path, "
            "type, description, allowed values). Use to discover what "
            "windyword.set_setting can change (theme, transcription model, "
            "widget size/position, language, etc.)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: _list_settings(),
        tier=Tier.READ_EXTERNAL,
    ))

    registry.register(Capability(
        id="windyword.set_setting",
        name="Set a Windy Word setting",
        description=(
            "Set one Windy Word setting by its catalog path (from "
            "windyword.list_settings), e.g. path='appearance.theme' "
            "value='dark', or path='engine.language' value='es'. Use for "
            "'switch to dark mode', 'make the tornado bigger' "
            "(path='tornadoSize'), 'change the language'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Setting path from list_settings."},
                "value": {"description": "New value (type per the setting)."},
            },
            "required": ["path", "value"],
        },
        handler=lambda **kw: _set_setting(**kw),
        tier=Tier.WRITE_LOCAL_SAFE,
    ))

    logger.info("Registered windyword.* capabilities (6 — app control bridge)")
