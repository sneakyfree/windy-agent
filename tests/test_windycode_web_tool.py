"""Tests for the windycode_web tools — the BROWSER-builder sibling of windycode.

Covers:
  - Registration adds all tools; client calls drift-tested against the
    vendored builder manifest (windy-code-web.mcp.v1)
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
    for var in ("WINDY_CODE_WEB_URL", "WINDY_CODE_WEB_DEFAULT",
                "ETERNITAS_PASSPORT_TOKEN", "WINDY_JWT", "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(var, raising=False)


def _mcp(body: dict, *, failed: bool = False, is_error: bool = False,
         status_code: int = 200) -> MagicMock:
    """A builder MCP tools/call response frame."""
    resp = MagicMock()
    resp.status_code = status_code
    content = {**body, "failed": True} if failed else body
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": body.get("speak", "")}],
        "structuredContent": content, "isError": is_error}}
    return resp


def _sent(post: MagicMock) -> tuple[str, dict]:
    """(MCP tool name, arguments) of the last call."""
    frame = post.call_args.kwargs["json"]
    assert frame["method"] == "tools/call"
    return frame["params"]["name"], frame["params"]["arguments"]


MCP_URL = f"{BASE}/api/v1/projects/mcp"
POST = "windyfly.tools.windycode_web.httpx.post"


def test_registration_adds_all_tools() -> None:
    registry = ToolRegistry()
    register_windycodeweb_tools(registry)
    names = {s["function"]["name"] for s in registry.get_schemas()}
    assert names == {
        "windycodeweb_status",
        "windycodeweb_list_projects",
        "windycodeweb_list_templates",
        "windycodeweb_start",
        "windycodeweb_start_from_template",
        "windycodeweb_create_project",
        "windycodeweb_add_files",
        "windycodeweb_list_editables",
        "windycodeweb_edit_text",
        "windycodeweb_list_checkpoints",
        "windycodeweb_undo",
        "windycodeweb_project_status",
        "windycodeweb_publish",
        "windycodeweb_preview",
        "windycodeweb_unpublish",
        "windycodeweb_connect_domain",
    }


def test_contract_drift_against_vendored_manifest() -> None:
    """Every builder tool + argument this client sends exists in the builder's
    published manifest (vendored from windy-code-web; re-vendor, never hand-edit)."""
    import json
    from pathlib import Path

    from windyfly.tools import windycode_web as mod

    manifest = json.loads((Path(mod.__file__).parent / "contracts"
                           / "windy-code-web.mcp.v1.json").read_text())
    assert manifest["contract"] == "windy-code-web.mcp.v1"
    schemas = {t["name"]: set(t["inputSchema"].get("properties", {})) for t in manifest["tools"]}
    for fly_tool, (tool, args) in mod._CONTRACT.items():
        assert tool in schemas, f"{fly_tool} calls {tool}, not in the builder manifest"
        assert args <= schemas[tool], f"{fly_tool} sends {sorted(args - schemas[tool])} to {tool}"


def test_unavailable_when_env_unset(no_builder_env: None) -> None:
    out = windycodeweb_list_projects()
    assert out["status"] == "unavailable"
    assert "WINDY_CODE_WEB_URL" in out["error"]


def test_no_default_url_while_dark(no_builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # WINDY_CODE_WEB_DEFAULT unset: a token alone must not send the EPT anywhere.
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.delenv("WINDY_CODE_WEB_DEFAULT", raising=False)
    with patch(POST) as post:
        out = windycodeweb_list_projects()
    assert out["status"] == "unavailable"
    post.assert_not_called()


def test_defaults_to_live_builder_when_flag_on(no_builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.setenv("WINDY_CODE_WEB_DEFAULT", "1")
    with patch(POST) as post:
        post.return_value = _mcp({"projects": []})
        out = windycodeweb_list_projects()
    assert out["status"] == "ok"
    assert post.call_args[0] == ("https://cloud.windycloud.com/api/v1/projects/mcp",)


def test_off_disables(no_builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.setenv("WINDY_CODE_WEB_DEFAULT", "1")
    monkeypatch.setenv("WINDY_CODE_WEB_URL", "off")
    with patch(POST) as post:
        out = windycodeweb_list_projects()
    assert out["status"] == "unavailable"
    post.assert_not_called()


def test_create_project_calls_mcp_with_bearer(builder_env: None) -> None:
    with patch(POST) as post:
        post.return_value = _mcp({"project": {"id": "p1"}, "speak": "“Garden Club” is ready."})
        out = windycodeweb_create_project("Garden Club")
    assert out["status"] == "ok"
    assert out["project"]["id"] == "p1"
    assert post.call_args[0] == (MCP_URL,)
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer ept_test_token"
    assert _sent(post) == ("create_project", {"name": "Garden Club", "kind": "site"})


def test_windy_jwt_fallback(builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.setenv("WINDY_JWT", "jwt_fallback")
    with patch(POST) as post:
        post.return_value = _mcp({"projects": []})
        windycodeweb_list_projects()
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer jwt_fallback"


def test_add_files_requires_label(builder_env: None) -> None:
    out = windycodeweb_add_files("p1", {"index.html": "<h1>hi</h1>"}, "   ")
    assert out["status"] == "failed"
    assert "label" in out["error"]


def test_add_files_merges_changed_files_only(builder_env: None) -> None:
    import base64

    with patch(POST) as post:
        post.return_value = _mcp({"checkpoint": {"version_id": "v1"}, "created": True,
                                  "mode": "merge", "files_in_version": 3, "speak": "Saved."})
        out = windycodeweb_add_files("p1", {"index.html": "<h1>hi</h1>"}, "Added the cover")
    assert out["status"] == "ok" and out["created"] is True
    tool, args = _sent(post)
    assert tool == "add_or_edit_files" and args["label"] == "Added the cover"
    assert args["mode"] == "merge" and "delete" not in args
    # the LLM sends TEXT; the wire carries BASE64 (the real sites cell decodes it)
    assert base64.b64decode(args["files"]["index.html"]).decode() == "<h1>hi</h1>"


def test_add_files_binary_delete_and_replace(builder_env: None) -> None:
    import base64

    raw = bytes(range(256)) + b"\x00\xff\x89PNG"   # every byte value, incl. non-UTF-8
    png = base64.b64encode(raw).decode()
    with patch(POST) as post:
        post.return_value = _mcp({"created": True, "speak": "Saved."})
        windycodeweb_add_files("p1", label="Added a photo", binary_files={"img/a.png": png},
                               delete=["old.html"], replace=True)
    _, args = _sent(post)
    assert args["files"]["img/a.png"] == png          # passed through, never re-encoded
    assert base64.b64decode(args["files"]["img/a.png"]) == raw   # byte-for-byte round trip
    assert args["delete"] == ["old.html"] and args["mode"] == "replace"


def test_add_files_rejects_bad_base64(builder_env: None) -> None:
    with patch(POST) as post:
        out = windycodeweb_add_files("p1", label="x", binary_files={"a.png": "not base64!!"})
    assert out["status"] == "failed" and "base64" in out["error"]
    post.assert_not_called()


def test_start_sends_prompt_and_fills(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_start

    with patch(POST) as post:
        post.return_value = _mcp({"project": {"id": "p9"}, "filled": ["headline"],
                                  "speak": "Started from the Recipe Book example."})
        out = windycodeweb_start("a recipe book", fills={"headline": "Nana's Kitchen", "intro": "  "})
    assert out["status"] == "ok"
    assert _sent(post) == ("start_from_prompt",
                           {"prompt": "a recipe book", "fills": {"headline": "Nana's Kitchen"}})


def test_edit_text_and_editables(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_edit_text, windycodeweb_list_editables

    with patch(POST) as post:
        post.return_value = _mcp({"editables": [{"edit_id": "headline", "value": "Hi"}]})
        assert windycodeweb_list_editables("p1")["editables"][0]["edit_id"] == "headline"
        assert _sent(post) == ("list_editables", {"project_id": "p1"})
        post.return_value = _mcp({"speak": "Changed the headline."})
        windycodeweb_edit_text("p1", "headline", "Welcome")
        assert _sent(post) == ("edit_text", {"project_id": "p1", "edit_id": "headline",
                                             "new_value": "Welcome"})


def test_builder_speak_errors_relayed(builder_env: None) -> None:
    with patch(POST) as post:
        post.return_value = _mcp({
            "code": "project_not_found",
            "speak": "I can't find that project under this account.",
            "remediation_tool": "list_projects",
        }, failed=True)
        out = windycodeweb_undo("nope", "cp1")
    assert out["status"] == "failed"
    assert out["speak"] == "I can't find that project under this account."
    assert out["remediation_tool"] == "list_projects"
    assert "failed" not in out


def test_daily_limit_is_the_builders_words(builder_env: None) -> None:
    speak = ("Your helper has done a lot of building today, so it's taking a break. "
             "It can carry on tomorrow, or you can do this yourself in the builder now.")
    with patch(POST) as post:
        post.return_value = _mcp({"code": "agent_daily_limit", "speak": speak}, failed=True)
        out = windycodeweb_add_files("p1", {"index.html": "x"}, "One more change")
    assert out["status"] == "failed" and out["code"] == "agent_daily_limit"
    assert out["speak"] == speak and out["retry"] == "tomorrow"


def test_auth_required_is_unavailable(builder_env: None) -> None:
    with patch(POST) as post:
        post.return_value = _mcp({"code": "auth_required", "speak": "sign in"}, is_error=True)
        assert windycodeweb_list_projects()["status"] == "unavailable"


def test_status_reports_missing_tools(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_status

    with patch(POST) as post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"tools": [{"name": "list_projects"}]}}
        post.return_value = resp
        out = windycodeweb_status()
    assert out["status"] == "degraded" and "start_from_prompt" in out["missing_tools"]


def test_publish_holds_the_token_and_never_shows_it(builder_env: None) -> None:
    from windyfly.tools import windycode_web as w

    w._HELD.clear()
    with patch(POST) as post:
        post.return_value = _mcp({
            "confirm_required": True,
            "confirm_token": "ct_abc",
            "speak": "Put “Garden Club” online for everyone to see?",
        })
        out = windycodeweb_publish("p1")
    assert out["status"] == "confirm_required" and out["done"] is False
    assert "ct_abc" not in str(out)  # the model never sees the token
    assert "yes, publish" in out["question"]
    assert w._HELD["publish"]["token"] == "ct_abc"


def test_model_replaying_a_token_is_refused(builder_env: None) -> None:
    with patch(POST) as post:
        out = windycodeweb_publish("p1", confirm_token="ct_abc")
    assert out["status"] == "refused" and out["done"] is False
    post.assert_not_called()  # nothing reached the builder


def test_publish_trust_denied(builder_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST")
    from windyfly.trust.gate import TrustDenied

    denied = TrustDenied(action="windycode_web_publish", band="critical",
                         reason="integrity band below floor")
    with patch(POST) as post, patch(
        "windyfly.trust.gate.require_trust", side_effect=denied
    ):
        out = windycodeweb_publish("p1")
    assert out["status"] == "denied"
    assert out["action"] == "windycode_web_publish"
    post.assert_not_called()  # denied publishes never reach the wire


def test_boot_registers_windycode_web() -> None:
    from windyfly.agent import boot

    assert hasattr(boot, "_step_register_windycode_web")
    src = open(boot.__file__, encoding="utf-8").read()
    assert 'Step("tools.windycode_web",  _step_register_windycode_web)' in src


def test_connect_domain_bundle_actions_pass_verbatim_without_a_model_token(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_connect_domain

    bundle = [{"type": "register_domain", "fqdn": "grandmarose.com"},
              {"type": "connect_domain_to_site", "fqdn": "grandmarose.com"}]
    with patch(POST) as post:
        post.return_value = _mcp({"confirm_required": True, "confirm_token": "bt_1",
                                  "speak": "Connect grandmarose.com?"})
        out = windycodeweb_connect_domain("p1", "GrandmaRose.com", bundle_actions=bundle)
    assert out["status"] == "confirm_required"
    _, sent = _sent(post)
    assert sent["fqdn"] == "grandmarose.com"
    assert "confirm_token" not in sent
    assert sent["bundle_actions"] == bundle  # untouched


def test_connect_domain_rejects_junk(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_connect_domain

    out = windycodeweb_connect_domain("p1", "nodots")
    assert out["status"] == "failed"


def test_unpublish_trust_denied_no_wire(builder_env: None,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from windyfly.tools.windycode_web import windycodeweb_unpublish
    from windyfly.trust.gate import TrustDenied

    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST")
    denied = TrustDenied(action="windycode_web_publish", band="critical",
                         reason="integrity band below floor")
    with patch(POST) as post, patch(
        "windyfly.trust.gate.require_trust", side_effect=denied
    ):
        out = windycodeweb_unpublish("p1")
    assert out["status"] == "denied"
    post.assert_not_called()


def test_preview_happy_path(builder_env: None) -> None:
    from windyfly.tools.windycode_web import windycodeweb_preview

    with patch(POST) as post:
        post.return_value = _mcp({"preview_url": "https://x/preview/t/index.html",
                                  "expires_in_seconds": 900})
        out = windycodeweb_preview("p1")
    assert out["status"] == "ok"
    assert out["preview_url"].startswith("https://x/preview/")
    assert _sent(post) == ("preview_project", {"project_id": "p1"})


def _prompt_system_text() -> str:
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.database import Database

    config = {
        "agent": {"default_model": "gpt-4o-mini"},
        "memory": {"db_path": ":memory:", "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "autonomy": 5},
    }
    msgs = assemble_prompt(config, Database(":memory:"), "make me a website", "s1")
    return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")


def test_prompt_rule_is_dark_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINDY_CODE_WEB_DEFAULT", raising=False)
    assert "BUILDING WEBSITES AND PAGES" not in _prompt_system_text()


def test_prompt_rule_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDY_CODE_WEB_DEFAULT", "1")
    assert "BUILDING WEBSITES AND PAGES" in _prompt_system_text()
