"""Tests for the news tool."""

from unittest.mock import MagicMock, patch

from windyfly.tools.news import _parse_rss, get_news

_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item><title>Breaking: AI Advances</title><link>https://example.com/1</link><description>Big news about AI</description></item>
<item><title>Weather Update</title><link>https://example.com/2</link><description>Sunny skies ahead</description></item>
<item><title>Sports Roundup</title><link>https://example.com/3</link><description>Scores from today</description></item>
</channel>
</rss>"""


def test_parse_rss():
    items = _parse_rss(_SAMPLE_RSS, "TestSource")
    assert len(items) == 3
    assert items[0]["title"] == "Breaking: AI Advances"
    assert items[0]["source"] == "TestSource"
    assert items[0]["url"] == "https://example.com/1"


def test_parse_rss_empty():
    items = _parse_rss("<rss><channel></channel></rss>", "Empty")
    assert items == []


def test_parse_rss_invalid():
    items = _parse_rss("not xml at all", "Bad")
    assert items == []


@patch("windyfly.tools.news.httpx.get")
def test_get_news_rss(mock_get):
    """Without NEWS_API_KEY, uses RSS feeds."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _SAMPLE_RSS
    mock_get.return_value = resp

    with patch.dict("os.environ", {}, clear=False):
        # Ensure no NEWS_API_KEY
        import os
        os.environ.pop("NEWS_API_KEY", None)
        result = get_news(count=3)

    assert "headlines" in result
    assert len(result["headlines"]) > 0


@patch("windyfly.tools.news.httpx.get")
def test_get_news_with_topic(mock_get):
    """Topic filtering selects appropriate feeds."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _SAMPLE_RSS
    mock_get.return_value = resp

    import os
    os.environ.pop("NEWS_API_KEY", None)
    result = get_news(topic="tech", count=3)
    assert "headlines" in result


@patch("windyfly.tools.news.httpx.get")
def test_get_news_api_failure(mock_get):
    """RSS fetch failure returns graceful message."""
    mock_get.side_effect = Exception("network error")
    import os
    os.environ.pop("NEWS_API_KEY", None)
    result = get_news()
    assert "headlines" in result
    # Should still return structure even if empty
    assert isinstance(result["headlines"], list)
