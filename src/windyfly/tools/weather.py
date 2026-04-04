"""Weather tool — "What's the weather in Fort Anne?"

Uses Open-Meteo API (free, no API key, no rate limit).
Geocodes city names, then fetches current conditions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# WMO weather codes → human descriptions
_WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "rime fog", 51: "light drizzle", 53: "moderate drizzle",
    55: "dense drizzle", 61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def get_weather(location: str) -> dict[str, Any]:
    """Get current weather for a location.

    Args:
        location: City name, e.g. "Fort Anne, NY" or "London".

    Returns:
        Weather data dict with temperature, conditions, humidity, wind.
    """
    # Step 1: Geocode the location
    coords = _geocode(location)
    if not coords:
        return {"error": f"Could not find location: {location}"}

    name, lat, lon, country = coords

    # Step 2: Fetch weather
    try:
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": f"Weather API error: {e}"}

    current = data.get("current_weather", {})
    daily = data.get("daily", {})

    temp = current.get("temperature", 0)
    code = current.get("weathercode", 0)
    wind = current.get("windspeed", 0)
    conditions = _WMO_CODES.get(code, "unknown")

    high = daily.get("temperature_2m_max", [None])[0]
    low = daily.get("temperature_2m_min", [None])[0]

    summary = f"It's {temp:.0f}°F and {conditions} in {name}."
    if high is not None:
        summary += f" High of {high:.0f}°F, low of {low:.0f}°F today."
    if wind > 0:
        summary += f" Wind: {wind:.0f} mph."

    return {
        "location": name,
        "country": country,
        "temperature_f": temp,
        "conditions": conditions,
        "wind_mph": wind,
        "high_f": high,
        "low_f": low,
        "summary": summary,
    }


def _geocode(location: str) -> tuple[str, float, float, str] | None:
    """Geocode a location name to coordinates."""
    try:
        resp = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        r = results[0]
        name = r.get("name", location)
        admin = r.get("admin1", "")
        if admin:
            name = f"{name}, {admin}"
        return name, r["latitude"], r["longitude"], r.get("country", "")
    except Exception as e:
        logger.debug("Geocoding failed: %s", e)
        return None


def register_weather_tool(registry: ToolRegistry) -> None:
    """Register weather tool with the LLM."""
    registry.register(
        name="get_weather",
        description=(
            "Get the current weather for a location. Use when the user asks "
            "'What's the weather?', 'Is it going to rain?', 'How cold is it in X?'. "
            "Works for any city worldwide. No API key needed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name, e.g. 'Fort Anne, NY' or 'London'",
                },
            },
            "required": ["location"],
        },
        fn=get_weather,
    )
