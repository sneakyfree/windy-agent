"""fetch_url is the obvious tool for reading a page (Windy Hand ask, 10-01)."""
import pytest

from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.tools import web_search
from windyfly.tools.registry import ToolRegistry


def _system(msg: str, band=None) -> str:
    config = {"agent": {"name": "Fly"}, "personality": {}}
    msgs = assemble_prompt(config, Database(":memory:"), msg, "s1", band=band)
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


def test_owner_and_user_band_get_the_hint():
    from windyfly.agent.capabilities import Band

    assert "LINK IN THIS MESSAGE" in _system("read https://example.com", band=Band.OWNER)
    assert "LINK IN THIS MESSAGE" in _system("read https://example.com", band=Band.USER)


def test_a_stranger_cannot_steer_a_fetch():
    from windyfly.agent.capabilities import Band

    assert "LINK IN THIS MESSAGE" not in _system("read https://evil.example/x", band=Band.SANDBOX)


def test_only_the_inbound_text_is_scanned():
    # A URL that arrives in a tool result or a fetched page is never part of
    # user_message, so it can't add the hint: the hint keys on user_message only.
    assert "LINK IN THIS MESSAGE" not in _system("thanks, what did that page say?")
