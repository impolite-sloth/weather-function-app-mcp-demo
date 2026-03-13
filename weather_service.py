import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class WeatherService:
    """Shared weather data access layer for Azure Functions MCP tools."""

    GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout_seconds: int = 10, max_retries: int = 2) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = self._build_session(max_retries)

    @staticmethod
    def _build_session(max_retries: int) -> requests.Session:
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=0.3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get_json(self, url: str, params: dict, operation: str) -> dict:
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            logging.warning("Weather API timed out during %s", operation)
            return {"error": "Weather provider request timed out"}
        except requests.RequestException:
            logging.exception("Weather API request failed during %s", operation)
            return {"error": "Weather provider request failed"}

    def _lookup_city(self, city: str) -> dict:
        data = self._get_json(
            self.GEO_URL,
            params={"name": city, "count": 1},
            operation="city lookup",
        )
        if "error" in data:
            return data

        if not data.get("results"):
            return {"error": f"City '{city}' not found"}

        return data["results"][0]

    def get_current_weather(self, city: str) -> dict:
        location = self._lookup_city(city)
        if "error" in location:
            return location

        weather_data = self._get_json(
            self.FORECAST_URL,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current_weather": "true",
            },
            operation="current weather",
        )
        if "error" in weather_data:
            return weather_data

        current = weather_data.get("current_weather", {})

        return {
            "city": location.get("name"),
            "country": location.get("country"),
            "temperature": current.get("temperature"),
            "temperature_unit": "C",
            "wind_speed": current.get("windspeed"),
            "wind_speed_unit": "km/h",
            "weather_code": current.get("weathercode"),
        }

    def get_weather_forecast(self, city: str, days: int = 7) -> dict:
        location = self._lookup_city(city)
        if "error" in location:
            return location

        days = max(1, min(days, 16))
        forecast_data = self._get_json(
            self.FORECAST_URL,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "forecast_days": days,
                "timezone": "auto",
            },
            operation="forecast",
        )
        if "error" in forecast_data:
            return forecast_data

        daily = forecast_data.get("daily", {})

        rows = []
        times = daily.get("time", [])
        max_values = daily.get("temperature_2m_max", [])
        min_values = daily.get("temperature_2m_min", [])
        precipitation_values = daily.get("precipitation_sum", [])
        code_values = daily.get("weathercode", [])

        for i, date in enumerate(times):
            rows.append(
                {
                    "date": date,
                    "temp_max": max_values[i] if i < len(max_values) else None,
                    "temp_min": min_values[i] if i < len(min_values) else None,
                    "precipitation": (
                        precipitation_values[i]
                        if i < len(precipitation_values)
                        else None
                    ),
                    "weather_code": code_values[i] if i < len(code_values) else None,
                }
            )

        return {
            "city": location.get("name"),
            "country": location.get("country"),
            "forecast": rows,
        }

    def compare_weather(self, cities: list[str]) -> dict:
        return {city: self.get_current_weather(city) for city in cities}

    @staticmethod
    def get_weather_description(weather_code: int) -> str:
        weather_codes = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow fall",
            73: "Moderate snow fall",
            75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail",
        }
        return weather_codes.get(weather_code, f"Unknown weather code: {weather_code}")
