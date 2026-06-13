"""OpenCV webcam presence detection."""

import logging
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Callable

from db import queries
from vital_types.db import PresenceConfig, PresenceLogEntry

from core.app_config import is_demo_mode
from core.notifications import send_notification
from core.tts import speak

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL_SECONDS = 300
DEFAULT_MAX_CONTINUOUS_MINUTES = 30
DEFAULT_BREAK_MESSAGE = (
    "You have been at your desk for a while. Stand up, stretch, and drink water."
)


def _default_check_once() -> bool:
    """Open the webcam, detect a face in one frame, then release the camera."""
    try:
        import cv2
    except ImportError:
        return False

    capture = cv2.VideoCapture(0)
    try:
        if not capture.isOpened():
            return False

        success, frame = capture.read()
        if not success:
            return False

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        return len(faces) > 0
    finally:
        capture.release()


def _camera_available() -> bool:
    """Return true when the webcam can be opened for a single frame."""
    try:
        import cv2
    except ImportError:
        return False

    capture = cv2.VideoCapture(0)
    try:
        if not capture.isOpened():
            return False
        success, _frame = capture.read()
        return success
    finally:
        capture.release()


def _resolve_presence_config() -> PresenceConfig:
    """Load presence settings from the database or return safe defaults."""
    config = queries.get_presence_config()
    if config is None:
        return PresenceConfig(
            enabled=True,
            max_continuous_minutes=DEFAULT_MAX_CONTINUOUS_MINUTES,
            break_message=DEFAULT_BREAK_MESSAGE,
            break_duration_minutes=5,
            check_interval_seconds=DEFAULT_CHECK_INTERVAL_SECONDS,
        )
    return config


class PresenceDetector:
    """Background desk-presence monitor with sit-too-long break reminders."""

    def __init__(
        self,
        config: PresenceConfig | None = None,
        check_once_fn: Callable[[], bool] | None = None,
    ):
        resolved_config = config or _resolve_presence_config()
        self.check_interval = resolved_config.check_interval_seconds
        self.max_continuous = resolved_config.max_continuous_minutes * 60
        self.break_message = resolved_config.break_message
        self.enabled = resolved_config.enabled
        self.continuous_seconds = 0
        self.running = False
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._check_once = check_once_fn or _default_check_once

    def check_once(self) -> bool:
        """Run a single presence check."""
        return self._check_once()

    def _log_presence(self, detected: bool) -> None:
        """Persist one presence check to the database."""
        continuous_minutes = self.continuous_seconds // 60
        queries.insert_presence_log(
            PresenceLogEntry(
                detected=detected,
                checked_at=datetime.now(timezone.utc),
                continuous_minutes=continuous_minutes,
            )
        )

    def trigger_break_reminder(self) -> None:
        """Fire a desk-break notification and optional TTS message."""
        from core.personalization import build_desk_break_message

        profile = queries.get_profile()
        break_minutes = self.max_continuous // 60
        if profile is not None and profile.name.strip():
            message = build_desk_break_message(profile.name, break_minutes)
        else:
            message = self.break_message or queries.get_presence_break_message()
        notification_result = send_notification(
            title="Vitál — Time for a Break",
            message=message,
            job_id="presence_break",
            log_delivery=True,
        )
        speak_result = speak(message, allow_repeat=True)
        if speak_result.spoken:
            logger.info("[presence] Break reminder spoken.")
        else:
            logger.info(
                "[presence] Break reminder not spoken: %s",
                speak_result.skipped_reason,
            )
        if not notification_result.delivered and not speak_result.spoken:
            logger.warning("[presence] Break reminder failed — no notification or TTS.")

    def run_loop(self) -> None:
        """Poll presence on an interval until stopped."""
        self.running = True
        while self.running:
            present = self.check_once()
            if present:
                self.continuous_seconds += self.check_interval
                if self.continuous_seconds >= self.max_continuous:
                    logger.info(
                        "[presence] Break threshold reached (%ss) — firing reminder.",
                        self.continuous_seconds,
                    )
                    self.trigger_break_reminder()
                    self.continuous_seconds = 0
            else:
                self.continuous_seconds = 0

            continuous_minutes = self.continuous_seconds // 60
            logger.info(
                "[presence] Check: detected=%s, continuous=%s min",
                present,
                continuous_minutes,
            )
            self._log_presence(present)
            if self._stop_event.wait(self.check_interval):
                break

    def start(self) -> None:
        """Start the background presence loop."""
        if self.running:
            return
        self._thread = Thread(target=self.run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background presence loop."""
        self.running = False
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)


_active_detector: PresenceDetector | None = None


def start_presence(
    check_once_fn: Callable[[], bool] | None = None,
) -> PresenceDetector | None:
    """Start presence detection when enabled and hardware is available."""
    global _active_detector

    if is_demo_mode():
        logger.info("Presence detection disabled in demo mode.")
        return None

    config = _resolve_presence_config()
    if not config.enabled:
        logger.info("Presence detection disabled by user configuration.")
        return None

    if check_once_fn is None and not _camera_available():
        logger.info("No camera found — presence detection disabled.")
        return None

    detector = PresenceDetector(
        config=config,
        check_once_fn=check_once_fn or _default_check_once,
    )
    detector.start()
    _active_detector = detector
    return detector


def restart_presence() -> PresenceDetector | None:
    """Stop the running presence loop and restart it with fresh DB config.

    Called after Settings save so threshold/message changes apply live.
    """
    global _active_detector

    if _active_detector is not None:
        _active_detector.stop()
        _active_detector = None
        logger.info("[presence] Stopped for settings reload.")

    detector = start_presence()
    if detector is not None:
        logger.info(
            "[presence] Restarted: break after %s min, check every %ss.",
            detector.max_continuous // 60,
            detector.check_interval,
        )
    return detector
