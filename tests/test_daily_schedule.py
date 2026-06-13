"""Tests for daily schedule generation and persistence."""

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from db import queries
from db.database import get_db_path, initialize_database, reset_database, set_db_path
from llm.daily_schedule import (
    build_template_fallback_daily_plan,
    copy_daily_plan_from_previous,
    should_generate_daily_plan,
    validate_daily_schedule_response,
)
from vital_types.daily_plan import DailyPlan, DailyScheduleJob
from vital_types.db import MedicationRecord, ProfileInput


class DailyScheduleTests(unittest.TestCase):
    """Cover daily plan validation, fallback, and DB round-trip."""

    def setUp(self) -> None:
        """Use an isolated database for each test."""
        self._db_path = get_db_path().parent / "test_daily_schedule.db"
        set_db_path(self._db_path)
        reset_database()

    def tearDown(self) -> None:
        """Reset connection after tests."""
        from db.database import close_connection
        close_connection()

    def _sample_profile(self) -> ProfileInput:
        """Return a minimal onboarded profile."""
        return ProfileInput(
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
            local_foods="beans",
        )

    def _sample_payload(self) -> dict[str, object]:
        """Return a valid daily schedule payload."""
        return {
            "summary": "Stay hydrated and move gently today.",
            "hydration_goal_liters": 3.0,
            "jobs": [
                {
                    "job_id": "water_0800",
                    "type": "hydration",
                    "time": "08:00",
                    "message": "Drink 375ml of water",
                    "volume_ml": 375,
                    "tts": True,
                    "context": "Hydration helps.",
                },
                {
                    "job_id": "water_1100",
                    "type": "hydration",
                    "time": "11:00",
                    "message": "Drink 375ml of water",
                    "volume_ml": 375,
                    "tts": True,
                    "context": "Hydration helps.",
                },
                {
                    "job_id": "water_1400",
                    "type": "hydration",
                    "time": "14:00",
                    "message": "Drink 375ml of water",
                    "volume_ml": 375,
                    "tts": True,
                    "context": "Hydration helps.",
                },
                {
                    "job_id": "water_1700",
                    "type": "hydration",
                    "time": "17:00",
                    "message": "Drink 375ml of water",
                    "volume_ml": 375,
                    "tts": True,
                    "context": "Hydration helps.",
                },
                {
                    "job_id": "breakfast_0800",
                    "type": "meal",
                    "time": "08:30",
                    "message": "Beans and plantain for folate",
                    "tts": True,
                    "context": "Breakfast.",
                },
                {
                    "job_id": "lunch_1300",
                    "type": "meal",
                    "time": "13:00",
                    "message": "Rice and vegetables",
                    "tts": True,
                    "context": "Lunch.",
                },
                {
                    "job_id": "dinner_1900",
                    "type": "meal",
                    "time": "19:00",
                    "message": "Light soup and fish",
                    "tts": True,
                    "context": "Dinner.",
                },
                {
                    "job_id": "walk_1700",
                    "type": "exercise",
                    "time": "17:00",
                    "message": "Gentle walk for 20 minutes",
                    "exercise_type": "walking",
                    "duration_minutes": 20,
                    "tts": True,
                    "context": "Low intensity only.",
                },
            ],
        }

    def test_validate_daily_schedule_response(self) -> None:
        """Validator should parse hydration, meals, exercise, and prep job."""
        profile = self._sample_profile()
        tomorrow = date.today() + timedelta(days=1)
        plan = validate_daily_schedule_response(self._sample_payload(), profile, tomorrow)
        self.assertGreaterEqual(len(plan.jobs), 6)
        self.assertTrue(any(job.job_id.startswith("exercise_prep_") for job in plan.jobs))
        self.assertTrue(plan.jobs[0].message.startswith("Eddy,"))

    def test_template_fallback_includes_meals(self) -> None:
        """Template fallback should create meals and exercise."""
        profile = self._sample_profile()
        queries.save_hydration_goal_liters(3.0)
        tomorrow = date.today() + timedelta(days=1)
        plan = build_template_fallback_daily_plan(profile, tomorrow)
        self.assertTrue(any(job.type == "meal" for job in plan.jobs))
        self.assertTrue(any(job.type == "exercise" for job in plan.jobs))
        exercise_times = [job.time for job in plan.jobs if job.type == "exercise"]
        self.assertNotIn("00:00", exercise_times)

    def test_copy_previous_daily_plan(self) -> None:
        """Copying yesterday's plan should preserve job times."""
        profile = self._sample_profile()
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        source = DailyPlan(
            plan_date=yesterday,
            summary="Yesterday plan",
            hydration_goal_liters=2.5,
            generated_at=date.today(),
            jobs=[
                DailyScheduleJob(
                    job_id="water_1000",
                    type="hydration",
                    time="10:00",
                    message="Drink water",
                    tts=True,
                    context="",
                    volume_ml=300,
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
        queries.save_daily_plan(source)
        copied = copy_daily_plan_from_previous(profile, today, source)
        self.assertIn("Carried over", copied.summary)
        self.assertTrue(any(job.time == "10:00" for job in copied.jobs))

    def test_save_and_load_daily_plan(self) -> None:
        """Daily plan should round-trip through the database."""
        profile = self._sample_profile()
        queries.save_profile(profile)
        queries.set_onboarding_complete(True)
        tomorrow = date.today() + timedelta(days=1)
        plan = build_template_fallback_daily_plan(profile, tomorrow)
        queries.save_daily_plan(plan)
        loaded = queries.get_daily_plan(tomorrow)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.jobs), len(plan.jobs))
        self.assertFalse(should_generate_daily_plan(tomorrow))

    def test_reschedule_past_jobs_for_today(self) -> None:
        """Past times in LLM output should be shifted forward, not rejected."""
        profile = self._sample_profile()
        today = date(2026, 6, 11)
        evening = datetime(2026, 6, 11, 18, 47)
        payload = self._sample_payload()
        with (
            patch("llm.daily_schedule.date") as mock_date,
            patch("llm.daily_schedule.datetime") as mock_datetime,
        ):
            mock_date.today.return_value = today
            mock_datetime.now.return_value = evening
            plan = validate_daily_schedule_response(payload, profile, today)
        job_times = [
            job.time for job in plan.jobs
            if not job.job_id.startswith("exercise_prep_")
        ]
        self.assertTrue(all(_time_at_or_after(time_value, "18:47") for time_value in job_times))

    def test_repair_pads_missing_meals_and_water(self) -> None:
        """A sparse LLM plan should be padded, not rejected."""
        profile = self._sample_profile()
        tomorrow = date.today() + timedelta(days=1)
        sparse_payload: dict[str, object] = {
            "summary": "Light evening focus with hydration.",
            "hydration_goal_liters": 2.5,
            "jobs": [
                {
                    "job_id": "water_0900",
                    "type": "hydration",
                    "time": "09:00",
                    "message": "Drink 400ml of water",
                    "volume_ml": 400,
                    "tts": True,
                    "context": "Hydration.",
                },
                {
                    "job_id": "walk_1700",
                    "type": "exercise",
                    "time": "17:00",
                    "message": "Gentle walk for 20 minutes",
                    "exercise_type": "walking",
                    "duration_minutes": 20,
                    "tts": True,
                    "context": "Low intensity.",
                },
            ],
        }
        plan = validate_daily_schedule_response(sparse_payload, profile, tomorrow)
        meal_jobs = [job for job in plan.jobs if job.type == "meal"]
        hydration_jobs = [job for job in plan.jobs if job.type == "hydration"]
        exercise_jobs = [job for job in plan.jobs if job.type == "exercise"]
        self.assertGreaterEqual(len(meal_jobs), 3)
        self.assertGreaterEqual(len(hydration_jobs), 4)
        self.assertEqual(len(exercise_jobs), 1)

    def test_clear_all_daily_schedules(self) -> None:
        """Clear helper should remove all schedule rows."""
        profile = self._sample_profile()
        plan = build_template_fallback_daily_plan(profile, date.today() + timedelta(days=1))
        queries.save_daily_plan(plan)
        removed = queries.clear_all_daily_schedules()
        self.assertGreater(removed, 0)
        self.assertFalse(queries.has_daily_plan(plan.plan_date))


def _time_at_or_after(time_value: str, minimum: str) -> bool:
    """Return True when time_value is at or after minimum (HH:MM)."""
    hour_a, minute_a = time_value.split(":", 1)
    hour_b, minute_b = minimum.split(":", 1)
    return int(hour_a) * 60 + int(minute_a) >= int(hour_b) * 60 + int(minute_b)


if __name__ == "__main__":
    initialize_database()
    unittest.main()
