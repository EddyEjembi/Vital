"""Tests for daily spoken greeting text."""

import unittest

from core.daily_greeting import build_spoken_greeting_text, period_of_day
from vital_types.db import ProfileInput


class DailyGreetingTests(unittest.TestCase):
    """Cover greeting phrasing."""

    def setUp(self) -> None:
        """Ensure database tables exist for plan lookups."""
        from db.database import initialize_database
        initialize_database()

    def test_period_of_day_afternoon(self) -> None:
        """Afternoon hours should map correctly."""
        from datetime import datetime
        afternoon = datetime(2026, 6, 8, 14, 0)
        self.assertEqual(period_of_day(afternoon), "afternoon")

    def test_spoken_greeting_uses_name_and_briefing(self) -> None:
        """Spoken greeting should include name and coach line."""
        profile = ProfileInput(
            name="Eddy",
            age=30,
            city="Abuja",
            profession="Engineer",
            goal="Wellness",
            conditions=[],
            medications=[],
            triggers=[],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="",
            local_foods="",
        )
        spoken = build_spoken_greeting_text(
            profile,
            "Stay hydrated and pace yourself today.",
        )
        self.assertIn("Eddy", spoken)
        self.assertIn("Stay hydrated", spoken)
        self.assertTrue(spoken.startswith("Good "))


if __name__ == "__main__":
    unittest.main()
