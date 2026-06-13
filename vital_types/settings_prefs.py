"""User preference types for settings."""

from dataclasses import dataclass


@dataclass
class TtsPreferences:
    """Per-category TTS toggles for scheduled notifications."""
    hydration: bool = True
    exercise: bool = True
    medication: bool = True
    meal: bool = True
    check_in: bool = True
