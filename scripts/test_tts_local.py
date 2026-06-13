"""
Manual Kokoro TTS test.

Usage:
    uv run python scripts/test_tts_local.py
"""

from db.database import initialize_database
from core.tts import speak

MESSAGE = "Hello Stan. Vitál is ready. Time to drink some water."


def main(MESSAGE: str) -> None:
    """Speak a short test phrase through Kokoro."""
    initialize_database()
    print(f"Speaking: {MESSAGE}")
    result = speak(MESSAGE, allow_long_text=True)
    print(f"Result: spoken={result.spoken}, engine={result.engine}, reason={result.skipped_reason}")


if __name__ == "__main__":
    main(MESSAGE)
