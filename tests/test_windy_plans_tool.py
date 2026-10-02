"""windy_plans: prices only from Windy's live plans endpoint, never guessed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from windyfly.tools import windy_plans as mod

LIVE = {"currency": "usd", "see_plans_url": "https://app.windyword.ai/upgrade", "plans": [
    {"id": "free", "display_name": "Free", "monthly_cents": 0, "annual_cents": 0,
     "cloud_session_minutes": 5, "cloud_devices": 1, "storage_bytes": 524288000},
    {"id": "pro", "display_name": "Windy Pro", "monthly_cents": 499, "annual_cents": 4900,
     "cloud_session_minutes": 15, "cloud_devices": 3, "storage_bytes": 5368709120},
    {"id": "hurricane", "display_name": "Windy Hurricane", "monthly_cents": None, "annual_cents": None,
     "cloud_session_minutes": 60, "cloud_devices": 100, "storage_bytes": 5497558138880},
]}


@pytest.fixture(autouse=True)
def _fresh_cache():
    mod._cache.update(at=0.0, data=None)
    yield
    mod._cache.update(at=0.0, data=None)


def _resp(status: int, body):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


def test_lines_come_from_the_endpoint():
    with patch.object(mod.httpx, "get", return_value=_resp(200, LIVE)) as get:
        out = mod.windy_plans()
    assert get.call_args.args[0] == mod.DEFAULT_PLANS_URL
    assert out["status"] == "ok" and out["see_plans_url"] == "https://app.windyword.ai/upgrade"
    assert out["plans"][0].startswith("Free: free (5-minute cloud sessions, 1 device")
    assert out["plans"][1] == "Windy Pro: $4.99/month or $49/year (15-minute cloud sessions, 3 devices, 5 GB storage)"
    assert out["plans"][2].startswith("Windy Hurricane: custom pricing")


def test_cached_for_an_hour():
    with patch.object(mod.httpx, "get", return_value=_resp(200, LIVE)) as get:
        mod.windy_plans()
        mod.windy_plans()
    assert get.call_count == 1


@pytest.mark.parametrize("effect", [httpx.ConnectError("down"), _resp(404, {"error": "nope"}),
                                    _resp(200, {"plans": []})])
def test_unavailable_never_guesses(effect):
    kw = {"side_effect": effect} if isinstance(effect, Exception) else {"return_value": effect}
    with patch.object(mod.httpx, "get", **kw):
        out = mod.windy_plans()
    assert out["status"] == "unavailable" and "$" not in out["error"]
    assert "app.windyword.ai/upgrade" in out["error"]


def test_registered_with_no_arguments():
    from windyfly.tools.registry import ToolRegistry

    reg = ToolRegistry()
    mod.register_windy_plans_tool(reg)
    names = {t["function"]["name"] for t in reg.get_schemas()}
    assert "windy_plans" in names
