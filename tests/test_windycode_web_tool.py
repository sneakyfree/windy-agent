"""Tests for the windycode_web tools — the BROWSER-builder sibling of windycode.

Covers:
  - Registration adds all eight tools
  - unavailable when env unset (never raises)
  - EPT bearer forwarded; WINDY_JWT fallback
  - create/add_files/undo/status happy paths hit the right endpoints
  - add_files insists on a non-empty human label
  - builder error bodies with grandma 'speak' are relayed verbatim
  - publish relays confirm_required untouched; passes confirm_token back
  - publish trust gate: TrustDenied → structured 'denied' (no HTTP call)
  - Boot sequence registers tools.windycode_web
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.windycode_web import (
    register_windycodeweb_tools,
    windycodeweb_add_files,
    windycodeweb_create_project,
    windycodeweb_list_projects,
    windycodeweb_publish,
    windycodeweb_undo,
)

BASE = "https://windycode.test"


@pytest.fixture
def builder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDY_CODE_WEB_URL", BASE)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)  # gate off in unit tests


@pytest.fixture
def no_builder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("WINDY_CODE_WEB_URL", "ETERNITAS_PASSPORT_TOKEN", "WINDY_JWT",
                "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(var, raising=False)


def _response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def test_registration_adds_all_tools() -> None:
    registry = ToolRegistry()
    register_windycodeweb_tools(registry)
    names = {s["function"]["name"] for s in registry.get_schemas()}
    assert names == {
        "windycodeweb_status",
        "windycodeweb_list_projects",
        "windycodeweb_create_project",
        "windycodeweb_add_files",
        "windycodeweb_list_checkpoints",
        "windycodeweb_undo",
        "windycodeweb_project_status",
        "windycodeweb_publish",
    }


def test_unavailable_when_env_unset(no_builder_env: None) -> None:
    out = windycodeweb_list_projects()
    assert out["status"] == "unavailable"
    assert "WINDY_CODE_WEB_URL" in out["error"]


def test_create_project_posts_with_bearer(builder_env: None) -> None:
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(
            200, {"project": {"id": "p1"}, "speak": "“Garden Club” is ready."}
        )
        out = windycodeweb_create_project("Garden Club")
    assert out["status"] == "ok"
    assert out["project"]["id"] == "p1"
    args, kwargs = req.call_args
    assert args == ("POST", f"{BASE}/api/v1/projects")
    assert kwargs["headers"]["Authorization"] == "Bearer ept_test_token"
    assert kwargs["json"] == {"name": "Garden Club", "kind": "site"}


def test_windy_jwt_fallback(builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.setenv("WINDY_JWT", "jwt_fallback")
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(200, {"projects": []})
        windycodeweb_list_projects()
    assert req.call_args.kwargs["headers"]["Authorization"] == "Bearer jwt_fallback"


def test_add_files_requires_label(builder_env: None) -> None:
    out = windycodeweb_add_files("p1", {"index.html": "<h1>hi</h1>"}, "   ")
    assert out["status"] == "failed"
    assert "label" in out["error"]


def test_add_files_happy_path(builder_env: None) -> None:
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(
            200, {"checkpoint": {"version_id": "v1"}, "created": True, "speak": "Saved."}
        )
        out = windycodeweb_add_files("p1", {"index.html": "<h1>hi</h1>"}, "Added the cover")
    assert out["status"] == "ok" and out["created"] is True
    args, kwargs = req.call_args
    assert args == ("POST", f"{BASE}/api/v1/projects/p1/checkpoints")
    assert kwargs["json"]["label"] == "Added the cover"


def test_builder_speak_errors_relayed(builder_env: None) -> None:
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(404, {"detail": {
            "code": "project_not_found",
            "speak": "I can't find that project under this account.",
            "remediation_tool": "list_projects",
        }})
        out = windycodeweb_undo("nope", "cp1")
    assert out["status"] == "failed"
    assert out["speak"] == "I can't find that project under this account."
    assert out["remediation_tool"] == "list_projects"


def test_publish_relays_confirm_required(builder_env: None) -> None:
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(200, {
            "confirm_required": True,
            "confirm_token": "ct_abc",
            "speak": "Put “Garden Club” online for everyone to see?",
        })
        out = windycodeweb_publish("p1")
    assert out["status"] == "ok"
    assert out["confirm_required"] is True
    assert out["confirm_token"] == "ct_abc"


def test_publish_passes_confirm_token(builder_env: None) -> None:
    with patch("windyfly.tools.windycode_web.httpx.request") as req:
        req.return_value = _response(200, {"state": "applying", "speak": "Going live."})
        windycodeweb_publish("p1", confirm_token="ct_abc")
    assert req.call_args.kwargs["json"] == {"confirm_token": "ct_abc"}


def test_publish_trust_denied(builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST")
    from windyfly.trust.gate import TrustDenied

    denied = TrustDenied(action="windycode_web_publish", band="critical",
                         reason="integrity band below floor")
    with patch("windyfly.tools.windycode_web.httpx.request") as req, patch(
        "windyfly.trust.gate.require_trust", side_effect=denied
    ):
        out = windycodeweb_publish("p1")
    assert out["status"] == "denied"
    assert out["action"] == "windycode_web_publish"
    req.assert_not_called()  # denied publishes never reach the wire


def test_boot_registers_windycode_web() -> None:
    from windyfly.agent import boot

    assert hasattr(boot, "_step_register_windycode_web")
    src = open(boot.__file__).read()
    assert 'Step("tools.windycode_web",  _step_register_windycode_web)' in src
