"""The Windy Code filing cabinet (contract v1.2), dark behind WINDY_CODE_CABINET=1.

Covers:
  - the three tools exist only with the flag on (and the prompt rule with them)
  - file_project / log_activity / list_cabinet send the v1.2 MCP names and args
  - links: https only, user:password@ refused, credential query params stripped
  - a note, summary or link label that looks like a secret is refused before any call
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools import windycode_web as mod
from windyfly.tools.registry import ToolRegistry

BASE = "https://windycode.test"
CABINET = {"windycodeweb_file_project", "windycodeweb_log_activity", "windycodeweb_list_cabinet"}


@pytest.fixture
def builder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDY_CODE_WEB_URL", BASE)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)


def _mcp(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": body.get("speak", "")}],
        "structuredContent": body, "isError": False}}
    return resp


def _names(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> set[str]:
    if flag is None:
        monkeypatch.delenv("WINDY_CODE_CABINET", raising=False)
    else:
        monkeypatch.setenv("WINDY_CODE_CABINET", flag)
    reg = ToolRegistry()
    mod.register_windycodeweb_tools(reg)
    return {t["function"]["name"] if "function" in t else t["name"] for t in reg.get_schemas()}


def test_cabinet_tools_are_dark_by_default(monkeypatch):
    assert not CABINET & _names(monkeypatch, None)
    assert not CABINET & _names(monkeypatch, "0")
    assert CABINET <= _names(monkeypatch, "1")


def test_prompt_rule_only_with_the_flag(monkeypatch):
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.database import Database

    def _prompt_system_text() -> str:
        config = {"agent": {"default_model": "gpt-4o-mini"},
                  "memory": {"db_path": ":memory:", "max_nodes_per_context": 10},
                  "personality": {"soul_path": "SOUL.md", "autonomy": 5}}
        msgs = assemble_prompt(config, Database(":memory:"), "set up a repo for my recipes", "s1")
        return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")

    monkeypatch.delenv("WINDY_CODE_CABINET", raising=False)
    assert "THE OWNER'S CABINET" not in _prompt_system_text()
    monkeypatch.setenv("WINDY_CODE_CABINET", "1")
    text = _prompt_system_text()
    assert "THE OWNER'S CABINET" in text and "never for answers or research" in text


def test_file_project_sends_v12_args(builder_env):
    with patch.object(mod, "_rpc", return_value=_mcp({"project_id": "p1", "created": True,
                                                       "speak": "Filed."})) as rpc:
        out = mod.windycodeweb_file_project(
            "Recipe app", "code_repo", summary="Grandma's recipes", ref="https://github.com/x/recipes",
            links=[{"kind": "repo", "url": "https://github.com/x/recipes", "label": "Code"}])
    assert out["status"] == "ok" and out["project_id"] == "p1"
    params = rpc.call_args.args[3]
    assert params["name"] == "file_project"
    assert params["arguments"] == {"name": "Recipe app", "kind": "code_repo",
                                   "summary": "Grandma's recipes", "ref": "https://github.com/x/recipes",
                                   "links": [{"kind": "repo", "url": "https://github.com/x/recipes",
                                              "label": "Code"}]}


def test_log_activity_and_list_cabinet_names(builder_env):
    with patch.object(mod, "_rpc", return_value=_mcp({"speak": "Noted."})) as rpc:
        assert mod.windycodeweb_log_activity("p1", "Added a search box  to the list.")["status"] == "ok"
        assert rpc.call_args.args[3] == {"name": "log_activity", "arguments": {
            "project_id": "p1", "speak": "Added a search box to the list."}}
        mod.windycodeweb_list_cabinet()
        assert rpc.call_args.args[3] == {"name": "list_cabinet", "arguments": {}}


@pytest.mark.parametrize("url, problem", [
    ("http://example.com/x", "https"),
    ("ftp://example.com/x", "https"),
    ("https://bob:hunter2@example.com/x", "password"),
])
def test_unsafe_links_are_refused_before_any_call(builder_env, url, problem):
    with patch.object(mod, "_rpc") as rpc:
        out = mod.windycodeweb_file_project("X", "other", links=[{"url": url}])
    assert out["status"] == "failed" and problem in out["error"]
    rpc.assert_not_called()


def test_credential_query_params_are_stripped(builder_env):
    with patch.object(mod, "_rpc", return_value=_mcp({"project_id": "p1"})) as rpc:
        mod.windycodeweb_file_project("Dash", "database", links=[
            {"kind": "dashboard", "url": "https://db.example.com/app?view=1&token=abc&api_key=k&Sig=s#frag"}])
    sent = rpc.call_args.args[3]["arguments"]["links"][0]["url"]
    assert sent == "https://db.example.com/app?view=1"


@pytest.mark.parametrize("call", [
    lambda: mod.windycodeweb_log_activity("p1", "Set the key to sk-proj-abcdefghijklmnopqrstuvwxyz123456"),
    lambda: mod.windycodeweb_file_project("X", "other", summary="Bearer abcdefghijklmnopqrstuvwxyz"),
    lambda: mod.windycodeweb_file_project("X", "other", links=[
        {"url": "https://x.example", "label": "sk-ant-abcdefghijklmnopqrstuvwxyz0123"}]),
])
def test_secret_looking_text_is_refused(builder_env, call):
    with patch.object(mod, "_rpc") as rpc:
        out = call()
    assert out["status"] == "failed" and "secret" in out["error"]
    rpc.assert_not_called()


def test_bad_kind_and_long_note_refused(builder_env):
    with patch.object(mod, "_rpc") as rpc:
        assert mod.windycodeweb_file_project("X", "spaceship")["status"] == "failed"
        assert mod.windycodeweb_log_activity("p1", "x" * 281)["status"] == "failed"
    rpc.assert_not_called()


def test_cabinet_contract_names_match_v12_spec():
    assert {t for t, _ in mod._CABINET_CONTRACT.values()} == {"file_project", "log_activity", "list_cabinet"}
    assert not set(mod._CABINET_CONTRACT) & set(mod._CONTRACT)
