"""Tests for weekly report scheduling and generation."""

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from db import queries
from db.database import close_connection, initialize_database, set_db_path
from vital_types.db import FoodLogEntry, ProfileInput, WeeklyReport

from core.weekly_startup import (
    is_weekly_report_due,
    pending_weekly_report_start,
)
from llm.weekly_report import (
    validate_weekly_report_response,
    generate_weekly_report,
)
from llm import daily_schedule as daily_schedule_module


class WeeklyReportTests(unittest.TestCase):
    """Cover weekly report validation, due logic, and persistence."""

    def setUp(self) -> None:
        """Use an isolated database."""
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_weekly.db"
        close_connection()
        set_db_path(db_path)
        initialize_database()

        queries.save_profile(
            ProfileInput(
                name="Eddy",
                age=30,
                city="Port Harcourt",
                profession="Engineer",
                goal="Manage wellness",
                conditions=["sickle cell"],
                medications=[],
                triggers=["dehydration"],
                wake_time="07:00",
                sleep_time="23:00",
                desk_worker=True,
                exercise_level="light",
                dietary_notes="",
                local_foods="beans, plantain",
            )
        )
        queries.set_onboarding_complete(True)
        queries.save_weekly_check_structure(
            {
                "report_day": "Sunday",
                "report_time": "20:00",
                "replan_day": "Sunday",
                "replan_time": "20:30",
            }
        )

    def tearDown(self) -> None:
        """Close the database."""
        close_connection()
        self.temp_dir.cleanup()

    def _seed_report_for_week(self, week_start: date) -> None:
        """Insert a placeholder report so catch-up logic skips that week."""
        queries.insert_weekly_report(
            WeeklyReport(
                week_start=week_start,
                report_text="Seeded report.",
                water_goals_hit=0,
                medication_adherence=0.0,
                exercises_completed=0,
                generated_at=datetime.now().astimezone(),
            )
        )

    def test_validate_weekly_report_response(self) -> None:
        """Valid JSON should produce a narrative with highlights."""
        text = validate_weekly_report_response(
            {
                "report_text": "You had a steady week with good hydration.",
                "highlights": ["Logged lunch twice", "Pain stayed low"],
                "focus_next_week": "Keep lunch logging consistent.",
            }
        )
        self.assertIn("steady week", text)
        self.assertIn("Highlights", text)
        self.assertIn("Focus next week", text)

    def test_pending_week_after_sunday_deadline(self) -> None:
        """A report should be pending after Sunday 20:00 if none exists."""
        week_start = queries.monday_of_week(date(2026, 6, 8))
        prior_week = week_start - timedelta(days=7)
        self._seed_report_for_week(prior_week)
        sunday_evening = datetime(2026, 6, 14, 21, 0)
        self.assertTrue(is_weekly_report_due(week_start, sunday_evening))
        pending = pending_weekly_report_start(sunday_evening)
        self.assertEqual(pending, week_start)

    def test_no_pending_before_sunday_deadline(self) -> None:
        """No report should be pending before this week's Sunday deadline."""
        week_start = queries.monday_of_week(date(2026, 6, 8))
        prior_week = week_start - timedelta(days=7)
        self._seed_report_for_week(prior_week)
        sunday_morning = datetime(2026, 6, 14, 10, 0)
        pending = pending_weekly_report_start(sunday_morning)
        self.assertIsNone(pending)

    def test_generate_weekly_report_fallback_only(self) -> None:
        """fallback_only should skip the LLM entirely."""
        week_start = queries.monday_of_week(date.today())
        report = generate_weekly_report(week_start, force=True, fallback_only=True)
        self.assertIsNotNone(report)
        assert report is not None
        self.assertIn("week in numbers", report.report_text.lower())

    def test_generate_weekly_report_fallback_on_llm_failure(self) -> None:
        """Template fallback should still persist a report when the LLM fails."""
        week_start = queries.monday_of_week(date.today())
        mock_client = MagicMock()
        mock_client.generate_onboarding_json.side_effect = ValueError("LLM unavailable")

        with patch("llm.weekly_report.get_llm_client", return_value=mock_client):
            report = generate_weekly_report(week_start, force=True)

        self.assertIsNotNone(report)
        assert report is not None
        self.assertIn("week in numbers", report.report_text.lower())

    def test_ensure_weekly_report_force(self) -> None:
        """Force mode should generate even when the Sunday deadline has not passed."""
        from core.weekly_startup import ensure_weekly_report

        wednesday = datetime(2026, 6, 10, 12, 0)
        mock_client = MagicMock()
        mock_client.generate_onboarding_json.return_value = {
            "report_text": "A solid mid-week test report with enough length to pass validation checks.",
            "highlights": ["Stayed hydrated"],
            "focus_next_week": "Log one meal per day.",
        }

        with patch("llm.weekly_report.get_llm_client", return_value=mock_client):
            generated = ensure_weekly_report(force=True, now=wednesday)

        self.assertTrue(generated)
        week_start = queries.monday_of_week(wednesday.date())
        self.assertIsNotNone(queries.get_weekly_report(week_start))

    def test_generate_weekly_report_with_mock_llm(self) -> None:
        """Mocked LLM output should persist a weekly report row."""
        week_start = queries.monday_of_week(date.today())
        queries.insert_food_log(
            FoodLogEntry(
                date=date.today(),
                meal_type="lunch",
                food_description="beans and plantain",
                logged_at=datetime.now().astimezone(),
            )
        )

        mock_client = MagicMock()
        mock_client.generate_onboarding_json.return_value = {
            "report_text": "Eddy, you logged one meal and stayed consistent this week.",
            "highlights": ["Logged lunch with beans and plantain"],
            "focus_next_week": "Log dinner twice next week.",
        }

        with patch("llm.weekly_report.get_llm_client", return_value=mock_client):
            report = generate_weekly_report(week_start, force=True)

        self.assertIsNotNone(report)
        assert report is not None
        self.assertIn("beans and plantain", report.report_text)
        stored = queries.get_weekly_report(week_start)
        self.assertIsNotNone(stored)

    def test_daily_schedule_context_includes_prior_week_meals(self) -> None:
        """Daily planner context should list meals eaten earlier in the week."""
        today = date.today()
        monday = queries.monday_of_week(today)
        if today <= monday:
            self.skipTest("Need a mid-week date to test prior-week meals.")

        queries.insert_food_log(
            FoodLogEntry(
                date=monday,
                meal_type="lunch",
                food_description="yam and egg",
                logged_at=datetime.now().astimezone(),
            )
        )
        profile = queries.get_profile()
        assert profile is not None
        context = daily_schedule_module.build_daily_schedule_context(profile, today)
        self.assertIn("yam and egg", context)
        self.assertIn("earlier this week", context)


if __name__ == "__main__":
    unittest.main()
