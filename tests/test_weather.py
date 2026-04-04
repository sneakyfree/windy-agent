"""Tests for the weather tool."""

from unittest.mock import MagicMock, patch

from windyfly.tools.weather import _geocode, get_weather


@patch("windyfly.tools.weather.httpx.get")
def test_get_weather_success(mock_get):
    """Mock geocoding + weather API responses."""
    geo_resp = MagicMock()
    geo_resp.status_code = 200
    geo_resp.json.return_value = {
        "results": [{"name": "Fort Anne", "admin1": "New York", "latitude": 43.4, "longitude": -73.5, "country": "US"}]
    }

    weather_resp = MagicMock()
    weather_resp.status_code = 200
    weather_resp.json.return_value = {
        "current_weather": {"temperature": 72.0, "weathercode": 1, "windspeed": 5.0},
        "daily": {"temperature_2m_max": [78.0], "temperature_2m_min": [55.0]},
    }

    mock_get.side_effect = [geo_resp, weather_resp]

    result = get_weather("Fort Anne, NY")
    assert result["temperature_f"] == 72.0
    assert "Fort Anne" in result["location"]
    assert "72°F" in result["summary"]
    assert result["conditions"] == "mainly clear"


@patch("windyfly.tools.weather.httpx.get")
def test_get_weather_unknown_location(mock_get):
    """Geocoding returns no results."""
    geo_resp = MagicMock()
    geo_resp.status_code = 200
    geo_resp.json.return_value = {"results": []}

    mock_get.return_value = geo_resp

    result = get_weather("Nonexistent Place XYZ")
    assert "error" in result


@patch("windyfly.tools.weather.httpx.get")
def test_get_weather_api_error(mock_get):
    """API connection failure."""
    mock_get.side_effect = Exception("connection refused")
    result = get_weather("London")
    assert "error" in result


@patch("windyfly.tools.weather.httpx.get")
def test_geocode_success(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [{"name": "London", "latitude": 51.5, "longitude": -0.1, "country": "UK"}]
    }
    mock_get.return_value = resp

    result = _geocode("London")
    assert result is not None
    assert result[0] == "London"
    assert result[3] == "UK"


@patch("windyfly.tools.weather.httpx.get")
def test_geocode_failure(mock_get):
    mock_get.side_effect = Exception("timeout")
    assert _geocode("Anywhere") is None
