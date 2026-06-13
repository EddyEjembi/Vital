"""Weather fetch via wttr.in with hourly caching."""

from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote

import requests

from db import queries
from vital_types.core import WeatherSnapshot

CACHE_TTL_SECONDS = 3600
UNKNOWN_WEATHER = WeatherSnapshot(
    city="unknown",
    condition="unknown",
    temp_c="?",
    feels_like_c="?",
    humidity="?",
    fetched_at=datetime.now(timezone.utc),
)

_cache: WeatherSnapshot | None = None
_cache_expires_at: datetime | None = None
_http_get: Callable[..., requests.Response] = requests.get


def _unknown_weather(city: str) -> WeatherSnapshot:
    """Return a safe fallback snapshot when weather cannot be fetched."""
    return WeatherSnapshot(
        city=city,
        condition="unknown",
        temp_c="?",
        feels_like_c="?",
        humidity="?",
        fetched_at=datetime.now(timezone.utc),
    )


def _parse_weather_payload(city: str, payload: dict) -> WeatherSnapshot:
    """Parse the wttr.in JSON payload into a WeatherSnapshot."""
    current = payload["current_condition"][0]
    return WeatherSnapshot(
        city=city,
        condition=current["weatherDesc"][0]["value"],
        temp_c=str(current["temp_C"]),
        feels_like_c=str(current["FeelsLikeC"]),
        humidity=str(current["humidity"]),
        fetched_at=datetime.now(timezone.utc),
    )


def fetch_weather_from_api(city: str, timeout_seconds: int = 5) -> WeatherSnapshot:
    """Fetch live weather for a city from wttr.in."""
    sanitized_city = city.strip()
    if not sanitized_city:
        return _unknown_weather("unknown")

    encoded_city = quote(sanitized_city)
    try:
        response = _http_get(
            f"https://wttr.in/{encoded_city}?format=j1",
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return _parse_weather_payload(sanitized_city, response.json())
    except Exception:
        return _unknown_weather(sanitized_city)


def _resolve_city(city: str | None) -> str | None:
    """Resolve the city from the argument or the saved user profile."""
    if city and city.strip():
        return city.strip()

    profile = queries.get_profile()
    if profile is None or not profile.city:
        return None
    return profile.city.strip()


def fetch_weather(city: str | None = None, force_refresh: bool = False) -> WeatherSnapshot:
    """Return cached weather when fresh, otherwise fetch and cache for one hour."""
    global _cache, _cache_expires_at

    resolved_city = _resolve_city(city)
    if not resolved_city:
        return UNKNOWN_WEATHER

    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _cache is not None
        and _cache_expires_at is not None
        and _cache.city == resolved_city
        and now < _cache_expires_at
    ):
        return _cache

    snapshot = fetch_weather_from_api(resolved_city)
    _cache = snapshot
    _cache_expires_at = now + timedelta(seconds=CACHE_TTL_SECONDS)
    return snapshot


def get_cached_weather() -> WeatherSnapshot | None:
    """Return the in-memory weather cache if it is still valid."""
    if _cache is None or _cache_expires_at is None:
        return None
    if datetime.now(timezone.utc) >= _cache_expires_at:
        return None
    return _cache


def clear_weather_cache() -> None:
    """Clear the in-memory weather cache (used by tests)."""
    global _cache, _cache_expires_at
    _cache = None
    _cache_expires_at = None
