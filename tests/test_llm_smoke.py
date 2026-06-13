"""Smoke tests for the Vitál LLM layer (mocked — no live Modal calls)."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from db.database import close_connection, initialize_database, set_db_path
from db import queries
from vital_types.db import MedicationLogEntry, ProfileInput

from core.weather import clear_weather_cache
from llm.client import LlmClient, reset_llm_client
from llm.config import LlmConfig
from llm.system_prompt import build_system_prompt
from llm.tool_runner import execute_tool
from llm.tools import TOOL_NAMES


class LlmSmokeTest(unittest.TestCase):
    """Verify tool schemas, runner validation, and client tool loop."""

    def setUp(self) -> None:
        """Use an isolated database for tool execution tests."""
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_vital.db"
        close_connection()
        set_db_path(db_path)
        initialize_database()
        reset_llm_client()
        clear_weather_cache()

        queries.save_profile(
            ProfileInput(
                name="Amara",
                age=24,
                city="Port Harcourt",
                profession="Student",
                goal="Manage a health condition",
                conditions=["sickle cell disease"],
                medications=[],
                triggers=["dehydration"],
                wake_time="07:00",
                sleep_time="23:00",
                desk_worker=True,
                exercise_level="light",
                dietary_notes="",
                local_foods="eba, egusi soup",
            )
        )

    def tearDown(self) -> None:
        """Close DB and reset client."""
        close_connection()
        reset_llm_client()
        clear_weather_cache()
        self.temp_dir.cleanup()

    def test_tool_names_count(self) -> None:
        """All registered tools should be present."""
        self.assertEqual(len(TOOL_NAMES), 12)
        self.assertIn("get_todays_schedule", TOOL_NAMES)

    def test_log_food_tool(self) -> None:
        """log_food should persist a meal to food_log."""
        result = execute_tool(
            "log_food",
            {"meal_type": "lunch", "food_description": "beans and plantain"},
        )
        self.assertTrue(result.success)
        entries = queries.get_food_logs_for_date(date.today())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].food_description, "beans and plantain")

    def test_log_water_tool(self) -> None:
        """log_water should write to daily logs."""
        result = execute_tool("log_water", {"cups": 4})
        self.assertTrue(result.success)
        logs = queries.get_daily_logs_for_date(date.today())
        water_logs = [entry for entry in logs if entry.field_id == "water_cups"]
        self.assertEqual(len(water_logs), 1)
        self.assertEqual(water_logs[0].value, "4")

    def test_log_water_rejects_invalid_cups(self) -> None:
        """Tool runner should reject out-of-range arguments."""
        result = execute_tool("log_water", {"cups": 99})
        self.assertFalse(result.success)

    def test_get_weekly_summary_tool(self) -> None:
        """Weekly summary tool should return structured stats."""
        result = execute_tool("get_weekly_summary", {"week": "current"})
        self.assertTrue(result.success)
        self.assertIn("week_start", result.result)

    def test_build_system_prompt_includes_profile(self) -> None:
        """System prompt should include the saved user profile."""
        prompt = build_system_prompt()
        self.assertIn("Amara", prompt)
        self.assertIn("sickle cell disease", prompt)
        self.assertIn("not a doctor", prompt.lower())

    def test_generate_json_parses_response(self) -> None:
        """JSON generation should parse a valid object from the model."""
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"follow_up_questions": []}',
                    tool_calls=None,
                )
            )
        ]

        config = LlmConfig(
            base_url="http://localhost:8000/v1",
            model_id="test-model",
            api_key="test",
            max_tokens=256,
            context_limit_tokens=8192,
            tool_temperature=0.6,
            json_temperature=0.4,
            max_tool_iterations=3,
            request_timeout_seconds=900.0,
            daily_schedule_max_attempts=3,
            daily_schedule_retry_delay_seconds=30.0,
            daily_schedule_max_tokens=4096,
        )
        client = LlmClient(config=config)
        client._openai = MagicMock()
        client._openai.chat.completions.create.return_value = mock_completion

        payload = client.generate_json('Return {"follow_up_questions": []}')
        self.assertIn("follow_up_questions", payload)

    def test_chat_runs_tool_loop(self) -> None:
        """Chat should execute a tool locally then return the final answer."""
        tool_function = MagicMock()
        tool_function.name = "get_todays_logs"
        tool_function.arguments = "{}"
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function = tool_function

        tool_response = MagicMock()
        tool_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="",
                    tool_calls=[tool_call],
                )
            )
        ]
        final_response = MagicMock()
        final_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="You have not logged anything yet today.",
                    tool_calls=None,
                )
            )
        ]

        config = LlmConfig(
            base_url="http://localhost:8000/v1",
            model_id="test-model",
            api_key="test",
            max_tokens=256,
            context_limit_tokens=8192,
            tool_temperature=0.6,
            json_temperature=0.4,
            max_tool_iterations=3,
            request_timeout_seconds=900.0,
            daily_schedule_max_attempts=3,
            daily_schedule_retry_delay_seconds=30.0,
            daily_schedule_max_tokens=4096,
        )
        client = LlmClient(config=config)
        client._openai = MagicMock()
        client._openai.chat.completions.create.side_effect = [tool_response, final_response]

        with patch("llm.system_prompt.fetch_weather") as mock_weather:
            mock_weather.return_value = MagicMock(
                city="Port Harcourt",
                condition="Cloudy",
                temp_c="28",
                feels_like_c="29",
                humidity="70",
            )
            answer = client.chat("What have I logged today?", use_tools=True)

        self.assertIn("not logged", answer.lower())
        self.assertEqual(client._openai.chat.completions.create.call_count, 2)

    def test_chat_retries_with_reply_schema_on_invalid_output(self) -> None:
        """An empty free-form reply should trigger the schema-constrained retry."""
        empty_response = MagicMock()
        empty_response.choices = [
            MagicMock(message=MagicMock(content="", tool_calls=None))
        ]
        constrained_response = MagicMock()
        constrained_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"reply": "You are doing well today, Amara."}',
                    tool_calls=None,
                )
            )
        ]

        config = LlmConfig(
            base_url="http://localhost:8000/v1",
            model_id="test-model",
            api_key="test",
            max_tokens=256,
            context_limit_tokens=8192,
            tool_temperature=0.6,
            json_temperature=0.4,
            max_tool_iterations=3,
            request_timeout_seconds=900.0,
            daily_schedule_max_attempts=3,
            daily_schedule_retry_delay_seconds=30.0,
            daily_schedule_max_tokens=4096,
        )
        client = LlmClient(config=config)
        client._openai = MagicMock()
        client._openai.chat.completions.create.side_effect = [
            empty_response,
            constrained_response,
        ]

        with patch("llm.system_prompt.fetch_weather") as mock_weather:
            mock_weather.return_value = MagicMock(
                city="Port Harcourt",
                condition="Cloudy",
                temp_c="28",
                feels_like_c="29",
                humidity="70",
            )
            answer = client.chat("How am I doing?", use_tools=True)

        self.assertEqual(answer, "You are doing well today, Amara.")
        self.assertEqual(client._openai.chat.completions.create.call_count, 2)
        # The retry must request the strict reply schema.
        retry_kwargs = client._openai.chat.completions.create.call_args_list[1].kwargs
        response_format = retry_kwargs.get("response_format", {})
        self.assertEqual(response_format.get("type"), "json_schema")

    def test_chat_fallback_logs_food_when_model_skips_write(self) -> None:
        """Coach should persist food locally when the model never calls log_food."""
        read_function = MagicMock()
        read_function.name = "get_todays_logs"
        read_function.arguments = "{}"
        read_tool = MagicMock()
        read_tool.id = "call_read"
        read_tool.function = read_function

        read_response = MagicMock()
        read_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="",
                    tool_calls=[read_tool],
                )
            )
        ]
        final_response = MagicMock()
        final_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="Great job logging your water!",
                    tool_calls=None,
                )
            )
        ]

        config = LlmConfig(
            base_url="http://localhost:8000/v1",
            model_id="test-model",
            api_key="test",
            max_tokens=256,
            context_limit_tokens=8192,
            tool_temperature=0.6,
            json_temperature=0.4,
            max_tool_iterations=2,
            request_timeout_seconds=900.0,
            daily_schedule_max_attempts=3,
            daily_schedule_retry_delay_seconds=30.0,
            daily_schedule_max_tokens=4096,
        )
        client = LlmClient(config=config)
        client._openai = MagicMock()
        client._openai.chat.completions.create.side_effect = [
            read_response,
            final_response,
        ]

        with patch("llm.system_prompt.fetch_weather") as mock_weather:
            mock_weather.return_value = MagicMock(
                city="Port Harcourt",
                condition="Cloudy",
                temp_c="28",
                feels_like_c="29",
                humidity="70",
            )
            answer = client.chat(
                "I just had lunch. I ate beans and plantain. Can you log that for me?",
                use_tools=True,
            )

        self.assertIn("beans and plantain", answer.lower())
        entries = queries.get_food_logs_for_date(date.today())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].meal_type, "lunch")


if __name__ == "__main__":
    unittest.main()
