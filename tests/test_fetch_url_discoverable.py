"""fetch_url is the obvious tool for reading a page (Windy Hand ask, 10-01)."""
import pytest

from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.tools import web_search
from windyfly.tools.registry import ToolRegistry


def _system(msg: str) -> str:
    config = {"agent": {"name": "Fly"}, "personality": {}}
    msgs = assemble_prompt(config, Database(":memory:"), msg, "s1")
    return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")


@pytest.mark.parametrize("msg", [
    "read this page: https://example.com/news",
    "what does http://example.org say about pricing?",
    "Can you summarize HTTPS://Example.com/a?b=1",
])
def test_a_link_in_the_message_steers_to_fetch_url(msg):
    s = _system(msg)
    assert "LINK IN THIS MESSAGE" in s and "fetch_url" in s


def test_no_link_no_hint():
    assert "LINK IN THIS MESSAGE" not in _system("what's the weather like?")


def test_descriptions_split_find_vs_read():
    reg = ToolRegistry()
    web_search.register_web_search_tool(reg)
    d = {t["function"]["name"]: t["function"]["description"] for t in reg.get_schemas()}
    assert "fetch_url" in d["web_search"]  # search points to fetch for reading
    assert "URL" in d["fetch_url"] and "auto" in d["fetch_url"]
    assert d["fetch_url"].startswith("READ") and d["web_search"].startswith("FIND")
