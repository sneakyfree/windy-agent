"""The agent's own view of Windy Mind: what powers it, what it may switch to, and the switch.

Contract: contract/mind/agent-self.v1.json (C16) + mind-refusal.v1.json, vendored from
windy-contracts and drift-tested (tests/test_mind_self_contract.py).

    GET    /v1/agents/me          -> AgentSelf (ETag W/"<config_version>", If-None-Match -> 304)
    PUT    /v1/agents/me/model    {"model": id} | {"chain": [ids]} -> AgentSelf
    DELETE /v1/agents/me/model    -> AgentSelf (back to the owner's setting)

The agent calls these with ITS OWN token (EPT+agent; PUT/DELETE carry a DPoP proof). The owner's
part is only the opt-in (``may_pick``). Mind takes exact catalog ids; the words people say
("groq", "Claude Haiku") are matched HERE, against ``may_pick.models`` only, and an ambiguous
word asks. No money appears in anything this module returns (Hub's ruling C).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from windyfly.agent import mind_auth

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_MIND_SELF"
REFRESH_S = 60.0          # how often the cached AgentSelf is re-checked (a 304 is cheap)
OFF_FOR_S = 600.0         # after a 404 (self-view/switch not on yet) stay quiet this long
_TIMEOUT_S = 5.0

_lock = threading.Lock()
_state: dict[str, Any] = {"self": None, "etag": None, "at": 0.0, "off_until": 0.0}



def enabled() -> bool:
    """On by default for an agent that has a Mind token; WINDY_MIND_SELF=0 turns it off.
    Tests never reach Mind unless they set the flag."""
    raw = os.environ.get(ENV_FLAG)
    if raw is None:
        return not os.environ.get("PYTEST_CURRENT_TEST")
    return raw.strip().lower() not in ("0", "false", "off", "no", "")


def _ept() -> str:
    return (os.environ.get("ETERNITAS_PASSPORT_TOKEN") or os.environ.get("ETERNITAS_PASSPORT") or "").strip()


def _base() -> str:
    from windyfly.agent.models import resolve_mind_url

    return resolve_mind_url().rstrip("/")


def _reset_for_tests() -> None:
    with _lock:
        _state.update({"self": None, "etag": None, "at": 0.0, "off_until": 0.0})


def _error(resp: Any) -> dict[str, Any]:
    try:
        env = (resp.json() or {}).get("error") or {}
        return env if isinstance(env, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ── reading ─────────────────────────────────────────────────────────────

def fetch(*, force: bool = False) -> dict[str, Any] | None:
    """The cached AgentSelf, refreshed at most every REFRESH_S. None when unavailable.
    Never raises."""
    if not enabled() or not _ept():
        return None
    now = time.time()
    with _lock:
        if now < _state["off_until"]:
            return None
        if not force and _state["at"] and now - _state["at"] < REFRESH_S:
            return _state["self"]
        etag = _state["etag"]
    try:
        hdrs = {"If-None-Match": etag} if etag else None
        resp = mind_auth.request("GET", f"{_base()}/v1/agents/me", _ept(), None, _TIMEOUT_S, hdrs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mind_self.fetch failed: %s", type(exc).__name__)
        with _lock:
            _state["at"] = now  # back off: a down Mind must not cost every turn a timeout
            return _state["self"]
    with _lock:
        if resp.status_code not in (200, 304, 404):
            _state["at"] = now
        if resp.status_code == 304:
            _state["at"] = now
        elif resp.status_code == 200:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                return _state["self"]
            _state.update({"self": body, "etag": resp.headers.get("etag"), "at": now})
        elif resp.status_code == 404:
            _state["off_until"] = now + OFF_FOR_S
            _state["self"] = None
        return _state["self"]


def _store(body: dict[str, Any], resp: Any) -> None:
    with _lock:
        _state.update({"self": body, "etag": resp.headers.get("etag"), "at": time.time()})


def picked_model() -> str | None:
    """The model someone picked in Windy Mind for this agent (the agent itself or its owner),
    else None. The loop sends it instead of the configured default."""
    s = fetch()
    if not s or s.get("picked_by") not in ("agent", "owner"):
        return None
    m = (s.get("effective") or {}).get("model")
    return str(m) if m else None


# ── plain words ─────────────────────────────────────────────────────────

def _plain_refusal(status: int, err: dict[str, Any]) -> str:
    code = str(err.get("code") or "")
    if code == "not_allowed_for_agent":
        allowed = err.get("allowed") or []
        if not allowed:
            return ("My owner hasn't let me choose my own model yet. They can allow it in Windy, "
                    "under 'Models my helpers may pick'.")
        return "I can't use that one. I can pick from: " + ", ".join(allowed[:8]) + "."
    if code == "switch_rate" or status == 429:
        mins = max(1, round(float(err.get("retry_after_s") or 60) / 60))
        return f"I've switched models a lot this hour; I can switch again in about {mins} minute{'s' if mins != 1 else ''}."
    if status == 404:
        return "Choosing my own model isn't turned on yet."
    if code == "agents_only":
        return "Only an agent can do that, not a person."
    return "I couldn't change my model just now. Windy Mind didn't accept it."


_UNAVAILABLE = {"ok": False, "say": "I couldn't reach Windy Mind just now. Try again in a moment."}


# ── the six tools ───────────────────────────────────────────────────────

def status() -> dict[str, Any]:
    s = fetch(force=True)
    if not s:
        return dict(_UNAVAILABLE)
    eff = s.get("effective") or {}
    mp = s.get("may_pick") or {}
    return {"ok": True, "state": s.get("state"), "model": eff.get("model"), "provider": eff.get("provider"),
            "chain": s.get("chain") or [], "picked_by": s.get("picked_by"),
            "may_pick_mode": mp.get("mode"), "source": "Windy Mind"}


def list_models() -> dict[str, Any]:
    s = fetch(force=True)
    if not s:
        return dict(_UNAVAILABLE)
    mp = s.get("may_pick") or {}
    models = list(mp.get("models") or [])
    out: dict[str, Any] = {"ok": True, "mode": mp.get("mode"), "models": models,
                           "current": (s.get("effective") or {}).get("model")}
    if mp.get("mode") == "none" or not models:
        out["say"] = ("My owner hasn't let me choose my own model yet. They can allow it in Windy, "
                      "under 'Models my helpers may pick'.")
    return out


def switch_model(model: str) -> dict[str, Any]:
    s = fetch(force=True)
    if not s:
        return dict(_UNAVAILABLE)
    mp = s.get("may_pick") or {}
    allowed = list(mp.get("models") or [])
    if mp.get("mode") == "none" or not allowed:
        return {"ok": False, "say": _plain_refusal(403, {"code": "not_allowed_for_agent", "allowed": []})}
    # The model passes the exact id from mind.list_models; the owner's words are its job, not ours.
    want = (model or "").strip().lower()
    exact = next((m for m in allowed if m.lower() == want), None)
    if exact is None:
        return {"ok": False, "say": "I can't use that one. I can pick from: " + ", ".join(allowed[:8]) + ".",
                "allowed": allowed}
    return _write("PUT", {"model": exact})


def reset_model() -> dict[str, Any]:
    return _write("DELETE", None)


def _write(method: str, body: dict[str, Any] | None) -> dict[str, Any]:
    if not enabled() or not _ept():
        return dict(_UNAVAILABLE)
    try:
        resp = mind_auth.request(method, f"{_base()}/v1/agents/me/model", _ept(), body, _TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mind_self write failed: %s", type(exc).__name__)
        return dict(_UNAVAILABLE)
    if resp.status_code != 200:
        return {"ok": False, "say": _plain_refusal(resp.status_code, _error(resp))}
    try:
        s = resp.json()
    except Exception:  # noqa: BLE001
        return dict(_UNAVAILABLE)
    _store(s, resp)
    try:  # the agent's own choice must show everywhere: drop per-channel /model pins
        from windyfly.agent.session_reset import clear_all_models

        clear_all_models()
    except Exception:  # noqa: BLE001
        pass
    eff = (s.get("effective") or {}).get("model")
    return {"ok": True, "model": eff, "picked_by": s.get("picked_by"),
            "say": (f"Done. I'm using {eff} from my next message." if body else
                    f"Done. I'm back to my owner's setting ({eff}) from my next message.")}


def my_usage() -> dict[str, Any]:
    s = fetch(force=True)
    if not s:
        return dict(_UNAVAILABLE)
    b = s.get("burn") or {}
    return {"ok": True, "window_hours": b.get("window_hours"), "calls": b.get("calls"),
            "tokens_in": b.get("tokens_in"), "tokens_out": b.get("tokens_out")}


_PAUSE_WORDS = {
    "grant_off": "my owner switched me off in Windy Mind",
    "token_paused": "my access was paused",
    "token_revoked": "my access was revoked",
    "ration_spent": "my free allowance for now is used up",
}


def why_paused() -> dict[str, Any]:
    s = fetch(force=True)
    if not s:
        return dict(_UNAVAILABLE)
    state = s.get("state")
    if state == "on":
        return {"ok": True, "paused": False, "say": "I'm not paused."}
    code = str(s.get("state_reason") or "")
    why = _PAUSE_WORDS.get(code) or "Windy Mind stopped me"
    return {"ok": True, "paused": True, "state": state, "reason_code": code or None,
            "say": f"I'm {state} because {why}."}
