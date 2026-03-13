from weather_service import WeatherService


def test_weather_code_description_known_value() -> None:
    assert WeatherService.get_weather_description(0) == "Clear sky"


def test_weather_code_description_unknown_value() -> None:
    assert "Unknown weather code" in WeatherService.get_weather_description(999)
