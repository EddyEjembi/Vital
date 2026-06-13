"""Tests for user-name personalization helpers."""

import unittest
from datetime import date, timedelta

from core.personalization import build_desk_break_message, first_name, personalize_message
from db.database import get_db_path, reset_database, set_db_path
from llm.daily_schedule import copy_daily_plan_from_previous
from vital_types.daily_plan import DailyPlan, DailyScheduleJob
from vital_types.db import ProfileInput


class PersonalizationTests(unittest.TestCase):
    """Cover name prefixing and plan finalization."""

    def setUp(self) -> None:
        """Use an isolated database for copy-plan tests."""
        self._db_path = get_db_path().parent / "test_personalization.db"
        set_db_path(self._db_path)
        reset_database()

    def tearDown(self) -> None:
        """Close DB connection after each test."""
        from db.database import close_connection
        close_connection()

    def test_first_name_extracts_token(self) -> None:
        """First name should be the leading token."""
        self.assertEqual(first_name("Eddy Okafor"), "Eddy")

    def test_personalize_message_prefixes_name(self) -> None:
        """Break messages should start with the user's first name."""
        result = personalize_message("Eddy Okafor", "Take a break and stretch.")
        self.assertEqual(result, "Eddy, Take a break and stretch.")

    def test_desk_break_message_template(self) -> None:
        """Desk break uses the fixed spoken template."""
        message = build_desk_break_message("Eddy", 30)
        self.assertIn("Eddy", message)
        self.assertIn("30 minutes", message)
        self.assertIn("stretch", message.lower())

    def test_copy_plan_strips_old_prep_jobs(self) -> None:
        """Copying should not duplicate exercise prep rows."""
        profile = ProfileInput(
            name="Eddy",
            age=30,
            city="Abuja",
            profession="Engineer",
            goal="Manage a health condition",
            conditions=["sickle cell"],
            medications=[],
            triggers=["dehydration"],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="",
            local_foods="",
        )
        source = DailyPlan(
            plan_date=date.today(),
            summary="Prior",
            hydration_goal_liters=2.5,
            generated_at=date.today(),
            jobs=[
                DailyScheduleJob(
                    job_id="exercise_prep_walk",
                    type="check_in",
                    time="16:50",
                    message="Prep",
                    tts=True,
                    context="",
                ),
                DailyScheduleJob(
                    job_id="walk_1700",
                    type="exercise",
                    time="17:00",
                    message="Walk",
                    tts=True,
                    context="",
                    exercise_type="walking",
                    duration_minutes=20,
                ),
            ],
        )
        copied = copy_daily_plan_from_previous(profile, date.today() + timedelta(days=1), source)
        prep_jobs = [job for job in copied.jobs if job.job_id.startswith("exercise_prep_")]
        self.assertEqual(len(prep_jobs), 1)
