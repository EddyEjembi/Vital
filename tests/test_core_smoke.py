"""Smoke tests for Day 2 core utilities."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from db.database import close_connection, get_connection, initialize_database, set_db_path
from db import queries
from vital_types.db import PresenceConfig, ProfileInput

from core import weather
from core.notifications import send_notification
from core.presence import PresenceDetector, start_presence
from core.tts import reset_tts_state, speak
from core.weather import clear_weather_cache, fetch_weather, fetch_weather_from_api


class CoreSmokeTest(unittest.TestCase):
    """Verify weather, notifications, TTS rules, and presence behaviour."""

    def setUp(self) -> None:
        """Use an isolated database and reset module caches."""
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_vital.db"
        close_connection()
        set_db_path(db_path)
        initialize_database()
        clear_weather_cache()
        reset_tts_state()
        weather._http_get = requests_get_mock = MagicMock()
        self.requests_get_mock = requests_get_mock

    def tearDown(self) -> None:
        """Restore environment and close the database."""
        close_connection()
        clear_weather_cache()
        reset_tts_state()
        if "DEMO_MODE" in os.environ:
            del os.environ["DEMO_MODE"]
        self.temp_dir.cleanup()

    def test_fetch_weather_from_api_parses_payload(self) -> None:
        """Weather API responses should parse into a typed snapshot."""
        self.requests_get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "current_condition": [
                    {
                        "weatherDesc": [{"value": "Sunny"}],
                        "temp_C": "31",
                        "FeelsLikeC": "34",
                        "humidity": "62",
                    }
                ]
            },
        )

        snapshot = fetch_weather_from_api("Port Harcourt")
        self.assertEqual(snapshot.city, "Port Harcourt")
        self.assertEqual(snapshot.condition, "Sunny")
        self.assertEqual(snapshot.temp_c, "31")

    def test_fetch_weather_uses_hourly_cache(self) -> None:
        """Repeated weather fetches within one hour should not call the API again."""
        self.requests_get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "current_condition": [
                    {
                        "weatherDesc": [{"value": "Cloudy"}],
                        "temp_C": "28",
                        "FeelsLikeC": "29",
                        "humidity": "70",
                    }
                ]
            },
        )

        first = fetch_weather("Lagos")
        second = fetch_weather("Lagos")
        self.assertEqual(first.condition, second.condition)
        self.requests_get_mock.assert_called_once()

    def test_fetch_weather_falls_back_on_api_error(self) -> None:
        """Weather fetch failures should return a safe unknown snapshot."""
        self.requests_get_mock.side_effect = Exception("network down")
        snapshot = fetch_weather_from_api("Lagos")
        self.assertEqual(snapshot.condition, "unknown")
        self.assertEqual(snapshot.temp_c, "?")

    @patch("core.notifications.is_demo_mode", return_value=False)
    @patch("plyer.notification.notify")
    def test_send_notification_logs_delivery(
        self,
        mock_notify: MagicMock,
        _mock_demo_mode: MagicMock,
    ) -> None:
        """Successful notifications should be logged in the database."""
        result = send_notification("Vitál", "Time for water", job_id="water_nudge_1")
        self.assertTrue(result.delivered)
        mock_notify.assert_called_once()

        connection = get_connection()
        rows = connection.execute("SELECT * FROM notifications_log;").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "water_nudge_1")

    @patch("core.notifications.is_demo_mode", return_value=True)
    def test_send_notification_skips_in_demo_mode(self, _mock_demo_mode: MagicMock) -> None:
        """Demo mode should suppress desktop notifications."""
        result = send_notification("Vitál", "Demo message")
        self.assertFalse(result.delivered)
        self.assertEqual(result.skipped_reason, "demo_mode")

    def test_sleep_window_with_midnight_bedtime(self) -> None:
        """Wake 07:00 / sleep 00:00 means awake 07:00-23:59, asleep 00:00-06:59."""
        from datetime import datetime

        from core.tts import _is_within_sleep_hours

        def at(hour: int, minute: int = 0) -> datetime:
            return datetime(2026, 6, 12, hour, minute)

        # Awake window: TTS allowed.
        self.assertFalse(_is_within_sleep_hours(at(7, 0), "07:00", "00:00"))
        self.assertFalse(_is_within_sleep_hours(at(12, 24), "07:00", "00:00"))
        self.assertFalse(_is_within_sleep_hours(at(23, 59), "07:00", "00:00"))
        # Sleep window: TTS suppressed.
        self.assertTrue(_is_within_sleep_hours(at(0, 0), "07:00", "00:00"))
        self.assertTrue(_is_within_sleep_hours(at(0, 1), "07:00", "00:00"))
        self.assertTrue(_is_within_sleep_hours(at(6, 59), "07:00", "00:00"))

    def test_sleep_window_with_pre_midnight_bedtime(self) -> None:
        """Wake 07:00 / sleep 23:00 means asleep 23:00-06:59 across midnight."""
        from datetime import datetime

        from core.tts import _is_within_sleep_hours

        def at(hour: int, minute: int = 0) -> datetime:
            return datetime(2026, 6, 12, hour, minute)

        self.assertFalse(_is_within_sleep_hours(at(7, 0), "07:00", "23:00"))
        self.assertFalse(_is_within_sleep_hours(at(22, 59), "07:00", "23:00"))
        self.assertTrue(_is_within_sleep_hours(at(23, 0), "07:00", "23:00"))
        self.assertTrue(_is_within_sleep_hours(at(2, 0), "07:00", "23:00"))
        self.assertTrue(_is_within_sleep_hours(at(6, 59), "07:00", "23:00"))

    def test_speak_blocks_during_sleep_hours(self) -> None:
        """TTS should not run during the user's configured sleep window."""
        with patch("core.tts.is_demo_mode", return_value=False):
            with patch("core.tts._is_sleep_time", return_value=True):
                blocked = speak("Hydration check")
            with patch("core.tts._is_sleep_time", return_value=False):
                with patch("core.tts._speak_with_kokoro", return_value=True):
                    allowed = speak("Hydration check")

        self.assertFalse(blocked.spoken)
        self.assertEqual(blocked.skipped_reason, "sleep_hours")
        self.assertTrue(allowed.spoken)

    def test_speak_allows_repeat_when_requested(self) -> None:
        """Presence-style reminders should bypass the duplicate cooldown."""
        message = "Eddy, time to stretch."
        with patch("core.tts.is_demo_mode", return_value=False):
            with patch("core.tts._is_sleep_time", return_value=False):
                with patch("core.tts._speak_with_kokoro", return_value=True) as mock_speak:
                    first = speak(message)
                    second = speak(message, allow_repeat=True)

        self.assertTrue(first.spoken)
        self.assertTrue(second.spoken)
        self.assertEqual(mock_speak.call_count, 2)

    def test_speak_blocks_recent_duplicate(self) -> None:
        """The same TTS message should not repeat within 30 minutes."""
        with patch("core.tts.is_demo_mode", return_value=False):
            with patch("core.tts._is_sleep_time", return_value=False):
                with patch("core.tts._speak_with_kokoro", return_value=True):
                    first = speak("Stand up and stretch")
                    second = speak("Stand up and stretch")

        self.assertTrue(first.spoken)
        self.assertFalse(second.spoken)
        self.assertEqual(second.skipped_reason, "recent_duplicate")

    def test_speak_blocks_long_messages(self) -> None:
        """Messages longer than 150 characters should not be spoken."""
        long_message = "a" * 151
        with patch("core.tts.is_demo_mode", return_value=False):
            with patch("core.tts._is_sleep_time", return_value=False):
                result = speak(long_message)

        self.assertFalse(result.spoken)
        self.assertEqual(result.skipped_reason, "message_too_long")

    @patch("core.presence.is_demo_mode", return_value=False)
    @patch("core.presence.send_notification")
    @patch("core.presence.speak")
    def test_presence_triggers_break_after_threshold(
        self,
        mock_speak: MagicMock,
        mock_send_notification: MagicMock,
        _mock_demo_mode: MagicMock,
    ) -> None:
        """Continuous presence should trigger a break reminder at the threshold."""
        mock_send_notification.return_value = MagicMock(delivered=True)
        mock_speak.return_value = MagicMock(spoken=False)

        config = PresenceConfig(
            enabled=True,
            max_continuous_minutes=1,
            break_message="Stretch time",
            break_duration_minutes=5,
            check_interval_seconds=60,
        )
        detector = PresenceDetector(
            config=config,
            check_once_fn=lambda: True,
        )
        detector.continuous_seconds = 60
        detector.trigger_break_reminder()

        mock_send_notification.assert_called_once()
        mock_speak.assert_called_once_with("Stretch time", allow_repeat=True)

    @patch("core.presence.is_demo_mode", return_value=False)
    def test_start_presence_uses_injected_check_for_tests(
        self,
        _mock_demo_mode: MagicMock,
    ) -> None:
        """Presence can start with an injected check function in tests."""
        queries.save_presence_config(
            PresenceConfig(
                enabled=True,
                max_continuous_minutes=30,
                break_message="Take a break",
                break_duration_minutes=5,
                check_interval_seconds=1,
            )
        )

        detector = start_presence(check_once_fn=lambda: True)
        self.assertIsNotNone(detector)
        assert detector is not None
        detector.stop()

    @patch("core.presence.is_demo_mode", return_value=True)
    def test_start_presence_disabled_in_demo_mode(self, _mock_demo_mode: MagicMock) -> None:
        """Demo mode should not start presence detection."""
        detector = start_presence(check_once_fn=lambda: True)
        self.assertIsNone(detector)


if __name__ == "__main__":
    unittest.main()
