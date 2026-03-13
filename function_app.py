import json
import logging
import os
from pathlib import Path

import azure.functions as func

from weather_service import WeatherService

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
weather_service = WeatherService(
    timeout_seconds=int(os.getenv("WEATHER_HTTP_TIMEOUT_SECONDS", "10"))
)

WEATHER_WIDGET_URI = "ui://weather/index.html"
WEATHER_WIDGET_NAME = "Weather Widget"
WEATHER_WIDGET_DESCRIPTION = "Interactive weather display for MCP Apps"
WEATHER_WIDGET_MIME_TYPE = "text/html;profile=mcp-app"

TOOL_METADATA = '{"ui": {"resourceUri": "ui://weather/index.html"}}'
RESOURCE_METADATA = '{"ui": {"prefersBorder": true}}'
MAX_COMPARE_CITIES = 10


def _json_response(payload: dict | str) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload)


def _clean_city(city: str) -> str:
    return (city or "").strip()


def _error_response(message: str) -> str:
    return _json_response({"error": message})


@app.mcp_resource_trigger(
    arg_name="context",
    uri=WEATHER_WIDGET_URI,
    resource_name=WEATHER_WIDGET_NAME,
    description=WEATHER_WIDGET_DESCRIPTION,
    mime_type=WEATHER_WIDGET_MIME_TYPE,
    metadata=RESOURCE_METADATA,
)
def get_weather_widget(context) -> str:
    """Serves the MCP Apps widget HTML."""
    widget_path = Path(__file__).parent / "app" / "dist" / "index.html"

    if widget_path.exists():
        return widget_path.read_text(encoding="utf-8")

    logging.warning("Widget file missing at %s", widget_path)
    return (
        "<!DOCTYPE html><html><head><title>Weather Widget</title></head>"
        "<body><h1>Weather Widget</h1><p>Widget build output not found.</p></body></html>"
    )


@app.mcp_tool(metadata=TOOL_METADATA)
@app.mcp_tool_property(
    arg_name="city",
    description="City name to check weather for (for example: Seattle, London, Tokyo)",
)
def get_current_weather(city: str) -> str:
    """Returns current weather for a city."""
    city_name = _clean_city(city)
    if not city_name:
        return _error_response("City is required")

    if len(city_name) > 100:
        return _error_response("City must be 100 characters or fewer")

    return _json_response(weather_service.get_current_weather(city_name))


@app.mcp_tool()
@app.mcp_tool_property(arg_name="city", description="City name to forecast")
@app.mcp_tool_property(arg_name="days", description="Forecast days (1-16)")
def get_weather_forecast(city: str, days: int = 7) -> str:
    """Returns a daily forecast for a city."""
    city_name = _clean_city(city)
    if not city_name:
        return _error_response("City is required")

    if len(city_name) > 100:
        return _error_response("City must be 100 characters or fewer")

    try:
        days_value = int(days)
    except (TypeError, ValueError):
        return _error_response("Days must be an integer between 1 and 16")

    if days_value < 1 or days_value > 16:
        return _error_response("Days must be between 1 and 16")

    return _json_response(weather_service.get_weather_forecast(city_name, days_value))


@app.mcp_tool()
@app.mcp_tool_property(
    arg_name="cities_csv",
    description="Comma-separated city names, for example: Seattle,London,Tokyo",
)
def compare_weather(cities_csv: str) -> str:
    """Compares current weather across multiple cities."""
    cities = [item.strip() for item in (cities_csv or "").split(",") if item.strip()]
    if not cities:
        return _error_response("Provide at least one city")

    if len(cities) > MAX_COMPARE_CITIES:
        return _error_response(
            f"Provide no more than {MAX_COMPARE_CITIES} cities in one request"
        )

    return _json_response(weather_service.compare_weather(cities))


@app.mcp_tool()
@app.mcp_tool_property(arg_name="weather_code", description="WMO weather code")
def get_weather_description(weather_code: int) -> str:
    """Converts a weather code to a readable description."""
    try:
        code = int(weather_code)
    except (TypeError, ValueError):
        return _error_response("weather_code must be an integer")

    return _json_response(
        {
            "weather_code": code,
            "description": weather_service.get_weather_description(code),
        }
    )
