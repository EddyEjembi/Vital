"""Tests for coach chat helpers."""

import unittest
from datetime import date, datetime, timezone

from db.database import close_connection, initialize_database, set_db_path
from db import queries
from vital_types.db import FoodLogEntry, ProfileInput
from vital_types.llm import ChatMessage

from llm.chat_helpers import (
    extract_food_description,
    recent_user_text,
    user_requests_food_log,
)
from llm.tool_runner import execute_tool


class ChatHelpersTest(unittest.TestCase):
    """Verify food-log intent detection and today's logs payload."""

    def setUp(self) -> None:
        """Use an isolated database."""
        import tempfile
        from pathlib import Path

        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_vital.db"
        close_connection()
        set_db_path(db_path)
        initialize_database()
        queries.save_profile(
            ProfileInput(
                name="Eddy",
                age=30,
                city="Port Harcourt",
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
                local_foods="beans, plantain",
            )
        )

    def tearDown(self) -> None:
        """Close the database."""
        close_connection()
        self.temp_dir.cleanup()

    def test_user_requests_food_log_detects_explicit_meal(self) -> None:
        """Explicit meal + log wording should request food logging."""
        message = "I just had lunch. I ate beans and plantain. Can you log that for me?"
        self.assertTrue(user_requests_food_log(message, None))

    def test_user_requests_food_log_detects_follow_up(self) -> None:
        """A follow-up 'log it' should use earlier meal context."""
        history = [
            ChatMessage(
                role="user",
                content="I just had lunch. I ate beans and plantain.",
            ),
        ]
        self.assertTrue(user_requests_food_log("Okay I just had it log it for me.", history))

    def test_extract_food_description_from_natural_language(self) -> None:
        """Parser should pull meal details from user text."""
        text = "I just had lunch. I ate beans and plantain. Can you log that for me?"
        self.assertEqual(extract_food_description(text), "beans and plantain")

    def test_recent_user_text_includes_prior_turns(self) -> None:
        """Recent user text should include earlier user messages."""
        history = [
            ChatMessage(role="user", content="I ate beans and plantain for lunch."),
        ]
        combined = recent_user_text("Please log it.", history)
        self.assertIn("beans and plantain", combined)
        self.assertIn("Please log it.", combined)

    def test_get_todays_logs_includes_food_logs(self) -> None:
        """Today's logs tool should expose persisted food entries."""
        queries.insert_food_log(
            FoodLogEntry(
                date=date.today(),
                meal_type="lunch",
                food_description="beans and plantain",
                logged_at=datetime.now(timezone.utc),
            )
        )
        result = execute_tool("get_todays_logs", {})
        self.assertTrue(result.success)
        food_logs = result.result.get("food_logs")
        self.assertIsInstance(food_logs, list)
        self.assertEqual(len(food_logs), 1)
        self.assertEqual(food_logs[0]["food_description"], "beans and plantain")


if __name__ == "__main__":
    unittest.main()
