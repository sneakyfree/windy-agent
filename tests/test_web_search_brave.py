"""Tests for upgraded web search (Brave + DuckDuckGo + fetch_url)."""

from unittest.mock import MagicMock, patch

from windyfly.tools.web_search import _brave_search, _ddg_search, fetch_url, web_search


@patch("windyfly.tools.web_search.httpx.get")
def test_brave_search_success(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "web": {
            "results": [
                {"title": "Result 1", "description": "Desc 1", "url": "https://example.com/1"},
                {"title": "Result 2", "description": "Desc 2", "url": "https://example.com/2"},
            ]
        }
    }
    mock_get.return_value = resp

    result = _brave_search("test query", limit=5)
    assert result["provider"] == "brave"
    assert len(result["results"]) == 2
    assert result["results"][0]["title"] == "Result 1"


@patch("windyfly.tools.web_search.httpx.get")
def test_brave_falls_back_to_ddg(mock_get):
    """When Brave fails, should fall back to DuckDuckGo."""
    # First call (Brave) fails, second call (DDG) succeeds
    ddg_resp = MagicMock()
    ddg_resp.status_code = 200
    ddg_resp.json.return_value = {
        "Abstract": "Test abstract",
        "Heading": "Test",
        "AbstractURL": "https://example.com",
        "RelatedTopics": [],
    }
    mock_get.side_effect = [Exception("Brave down"), ddg_resp]

    result = _brave_search("test", limit=5)
    assert result["provider"] == "duckduckgo"


@patch("windyfly.tools.web_search.httpx.get")
def test_ddg_search(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "Abstract": "Python is a programming language",
        "Heading": "Python",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [],
    }
    mock_get.return_value = resp

    result = _ddg_search("python")
    assert result["provider"] == "duckduckgo"
    assert len(result["results"]) >= 1


def test_web_search_selects_provider():
    """web_search() picks Brave if key is set, DDG otherwise."""
    with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": ""}, clear=False):
        with patch("windyfly.tools.web_search._ddg_search") as mock_ddg:
            mock_ddg.return_value = {"query": "test", "results": []}
            web_search("test")
            mock_ddg.assert_called_once()

    with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "test-key"}):
        with patch("windyfly.tools.web_search._brave_search") as mock_brave:
            mock_brave.return_value = {"query": "test", "results": []}
            web_search("test")
            mock_brave.assert_called_once()


@patch("windyfly.tools.web_search.httpx.get")
def test_fetch_url_success(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
    mock_get.return_value = resp

    result = fetch_url("https://example.com")
    assert "Hello world" in result["content"]
    assert "Title" in result["content"]
    assert "<html>" not in result["content"]  # HTML stripped


@patch("windyfly.tools.web_search.httpx.get")
def test_fetch_url_truncation(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<p>" + "x" * 10000 + "</p>"
    mock_get.return_value = resp

    result = fetch_url("https://example.com", max_chars=100)
    assert len(result["content"]) == 100
    assert result["truncated"] is True


@patch("windyfly.tools.web_search.httpx.get")
def test_fetch_url_error(mock_get):
    mock_get.side_effect = Exception("connection refused")
    result = fetch_url("https://nonexistent.example")
    assert "error" in result
