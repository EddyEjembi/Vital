from dataclasses import dataclass
from datetime import datetime


@dataclass
class WeatherSnapshot:
    """Current weather conditions for a city."""
    city: str
    condition: str
    temp_c: str
    feels_like_c: str
    humidity: str
    fetched_at: datetime


@dataclass
class SpeakResult:
    """Outcome of a TTS speak attempt."""
    spoken: bool
    skipped_reason: str | None = None
    engine: str | None = None


@dataclass
class NotificationResult:
    """Outcome of a desktop notification attempt."""
    delivered: bool
    skipped_reason: str | None = None
