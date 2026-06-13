"""Local TTS for voice notifications via Kokoro (hexgrad/Kokoro-82M)."""

import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

import numpy as np
import sounddevice as sd

from db import queries
from vital_types.core import SpeakResult

from core.app_config import is_demo_mode

logger = logging.getLogger(__name__)

MAX_TTS_LENGTH = 150
REPEAT_COOLDOWN_MINUTES = 30
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG_CODE = "a"
KOKORO_SAMPLE_RATE = 24000

_pipeline: Any | None = None
_last_spoken_messages: dict[str, datetime] = {}

# Serializes audio playback so concurrent reminders (e.g. a hydration job and a
# presence break firing together) queue instead of cutting each other off.
_audio_lock = Lock()


def _parse_time_to_minutes(time_value: str) -> int | None:
    """Convert an HH:MM string into minutes since midnight."""
    try:
        hours_text, minutes_text = time_value.split(":")
        return int(hours_text) * 60 + int(minutes_text)
    except (AttributeError, TypeError, ValueError):
        return None


def _is_within_sleep_hours(now: datetime, wake_time: str, sleep_time: str) -> bool:
    """Return true when the current local time falls inside sleep hours.

    The sleep window runs from sleep_time to wake_time. Two layouts:
    - Sleep before midnight (wake 07:00, sleep 23:00): asleep when
      current >= 23:00 OR current < 07:00 (window crosses midnight).
    - Sleep at/after midnight (wake 07:00, sleep 00:00): asleep when
      00:00 <= current < 07:00 (window within one day).
    """
    wake_minutes = _parse_time_to_minutes(wake_time)
    sleep_minutes = _parse_time_to_minutes(sleep_time)
    if wake_minutes is None or sleep_minutes is None:
        return False

    current_minutes = now.hour * 60 + now.minute
    if sleep_minutes < wake_minutes:
        return sleep_minutes <= current_minutes < wake_minutes
    return current_minutes >= sleep_minutes or current_minutes < wake_minutes


def _is_sleep_time(now: datetime | None = None) -> bool:
    """Return true when TTS should be suppressed due to sleep hours."""
    profile = queries.get_profile()
    if profile is None:
        return False
    current_time = now or datetime.now()
    return _is_within_sleep_hours(current_time, profile.wake_time, profile.sleep_time)


def _was_spoken_recently(message: str, now: datetime) -> bool:
    """Return true if the same message was spoken within the cooldown window."""
    last_spoken_at = _last_spoken_messages.get(message)
    if last_spoken_at is None:
        return False
    return now - last_spoken_at < timedelta(minutes=REPEAT_COOLDOWN_MINUTES)


def _record_spoken_message(message: str, spoken_at: datetime) -> None:
    """Track a spoken message for repeat suppression."""
    _last_spoken_messages[message] = spoken_at


def _get_pipeline() -> Any:
    """Lazy-load the Kokoro KPipeline (downloads model on first run via HF cache)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    logger.info("Loading Kokoro TTS — first run may download the model (~80MB).")
    from kokoro import KPipeline

    _pipeline = KPipeline(lang_code=DEFAULT_LANG_CODE)
    logger.info("Kokoro TTS ready.")
    return _pipeline


def _generate_audio_samples(text: str, voice: str) -> np.ndarray | None:
    """Run Kokoro inference and return concatenated audio samples."""
    pipeline = _get_pipeline()
    generator = pipeline(text, voice=voice)
    chunks: list[np.ndarray] = []

    for _index, (_graphemes, _phonemes, audio) in enumerate(generator):
        chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        return None
    return np.concatenate(chunks)


def _speak_with_kokoro(text: str, voice: str) -> bool:
    """Synthesize and play speech using Kokoro (one message at a time)."""
    try:
        samples = _generate_audio_samples(text, voice)
        if samples is None or samples.size == 0:
            return False

        with _audio_lock:
            sd.play(samples, KOKORO_SAMPLE_RATE)
            sd.wait()
        return True
    except Exception as error:
        logger.warning("Kokoro TTS failed: %s", error)
        return False


def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    allow_long_text: bool = False,
    allow_repeat: bool = False,
) -> SpeakResult:
    """Speak a short notification message, applying Vitál TTS rules."""
    if is_demo_mode():
        return SpeakResult(spoken=False, skipped_reason="demo_mode")

    trimmed_text = text.strip()
    if not trimmed_text:
        return SpeakResult(spoken=False, skipped_reason="empty_message")

    if not allow_long_text and len(trimmed_text) > MAX_TTS_LENGTH:
        return SpeakResult(spoken=False, skipped_reason="message_too_long")

    now = datetime.now()
    if _is_sleep_time(now):
        return SpeakResult(spoken=False, skipped_reason="sleep_hours")

    if not allow_repeat and _was_spoken_recently(trimmed_text, now):
        logger.info("[tts] Skipped (recent duplicate): %s", trimmed_text[:60])
        return SpeakResult(spoken=False, skipped_reason="recent_duplicate")

    if _speak_with_kokoro(trimmed_text, voice):
        _record_spoken_message(trimmed_text, now)
        logger.info("[tts] Spoke (%s chars).", len(trimmed_text))
        return SpeakResult(spoken=True, engine="kokoro")

    logger.warning("[tts] Kokoro playback failed.")
    return SpeakResult(spoken=False, skipped_reason="kokoro_failed")


def reset_tts_state() -> None:
    """Reset repeat-suppression and pipeline state (used by tests)."""
    global _pipeline
    _last_spoken_messages.clear()
    _pipeline = None
