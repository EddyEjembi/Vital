"""Smoke tests for onboarding commit and daily row seeding."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from db.database import close_connection, initialize_database, set_db_path
from db import queries
from llm.onboarding import validate_follow_up_response, validate_plan_response
from vital_types.db import (
    DailyLogSchemaField,
    MedicationRecord,
    PresenceConfig,
    ProfileInput,
    ScheduledJob,
)
from vital_types.onboarding import (
    ExercisePlan,
    MealPlanFramework,
    OnboardingCommitData,
    OnboardingPlan,
    WeeklyCheckStructure,
)


class Day4SmokeTest(unittest.TestCase):
    """Verify onboarding validation, commit, and ensure_daily_rows."""

    def setUp(self) -> None:
        """Use an isolated temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_vital.db"
        close_connection()
        set_db_path(db_path)
        initialize_database()

    def tearDown(self) -> None:
        """Close the connection and remove the temporary database."""
        close_connection()
        self.temp_dir.cleanup()

    def test_validate_follow_up_response_caps_at_three(self) -> None:
        """Follow-up validation should keep only the first three questions."""
        payload = {
            "follow_up_questions": [
                {
                    "question_id": f"q_{index}",
                    "question": f"Question {index}?",
                    "type": "text",
                    "reason": "test",
                }
                for index in range(5)
            ]
        }
        questions = validate_follow_up_response(payload)
        self.assertEqual(len(questions), 3)

    def test_validate_plan_response_normalizes_jobs(self) -> None:
        """Plan validation should infer daily_time schedule for timed jobs."""
        payload = {
            "daily_log_fields": [
                {
                    "field_id": "pain_level",
                    "label": "Pain level",
                    "type": "scale_1_10",
                    "options": [],
                    "reason": "monitor pain",
                }
            ],
            "scheduled_jobs": [
                {
                    "job_id": "med_morning",
                    "type": "medication",
                    "time": "08:00",
                    "days": "daily",
                    "message": "Take folic acid",
                    "tts": True,
                    "context": "daily med",
                }
            ],
            "meal_plan_framework": {
                "nutrients_to_prioritise": ["folate"],
                "nutrients_to_moderate": ["sugar"],
                "meal_frequency": 3,
                "notes": "eat local foods",
            },
            "exercise_plan": {
                "frequency": "daily",
                "intensity": "low",
                "session_duration_minutes": 20,
                "types": ["walking"],
                "avoid": ["sprints"],
                "notes": "stay safe",
            },
            "weekly_check_structure": {
                "report_day": "Sunday",
                "report_time": "20:00",
                "replan_day": "Sunday",
                "replan_time": "20:30",
            },
            "presence_check": {
                "enabled": True,
                "max_continuous_minutes": 30,
                "break_message": "Take a break",
                "break_duration_minutes": 5,
            },
            "system_prompt_additions": "Hydration matters.",
            "coach_quick_questions": ["What should I eat?"],
        }
        plan = validate_plan_response(payload)
        self.assertEqual(plan.scheduled_jobs[0].schedule_type, "daily_time")
        self.assertEqual(plan.daily_log_fields[0].field_id, "pain_level")

    def test_commit_onboarding_plan_writes_all_sections(self) -> None:
        """Atomic onboarding commit should populate profile, schema, and settings."""
        profile = ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
            conditions=["sickle cell disease"],
            medications=[
                MedicationRecord(name="Folic acid", dose="5mg", time="08:00"),
            ],
            triggers=["dehydration"],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="Low sugar",
            local_foods="beans",
        )
        plan = OnboardingPlan(
            daily_log_fields=[
                DailyLogSchemaField(
                    field_id="pain_level",
                    label="Pain level",
                    type="scale_1_10",
                    display_order=0,
                    reason="track pain",
                )
            ],
            scheduled_jobs=[
                ScheduledJob(
                    job_id="folic_acid",
                    type="medication",
                    schedule_type="daily_time",
                    time="08:00",
                    interval_minutes=None,
                    days="daily",
                    message="Take folic acid",
                    tts=True,
                    active=True,
                    context="daily supplement",
                )
            ],
            meal_plan_framework=MealPlanFramework(
                nutrients_to_prioritise=["folate"],
                nutrients_to_moderate=["sugar"],
                meal_frequency=3,
                notes="local foods",
            ),
            exercise_plan=ExercisePlan(
                frequency="daily",
                intensity="low",
                session_duration_minutes=20,
                types=["walking"],
                avoid=["sprints"],
                notes="gentle movement",
            ),
            weekly_check_structure=WeeklyCheckStructure(
                report_day="Sunday",
                report_time="20:00",
                replan_day="Sunday",
                replan_time="20:30",
            ),
            presence_check=PresenceConfig(
                enabled=True,
                max_continuous_minutes=30,
                break_message="Stretch now",
                break_duration_minutes=5,
            ),
            system_prompt_additions="Hydration first.",
            coach_quick_questions=["Crisis signs?"],
        )

        queries.commit_onboarding_plan(OnboardingCommitData(profile=profile, plan=plan))

        self.assertTrue(queries.check_onboarding_status())
        self.assertEqual(len(queries.get_daily_log_schema()), 1)
        self.assertEqual(len(queries.get_all_scheduled_jobs()), 1)
        self.assertEqual(queries.get_system_prompt_additions(), "Hydration first.")
        self.assertEqual(queries.get_coach_quick_questions(), ["Crisis signs?"])

    def test_ensure_daily_rows_is_idempotent(self) -> None:
        """ensure_daily_rows should seed medication rows once per day."""
        profile = ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
            conditions=[],
            medications=[
                MedicationRecord(name="Folic acid", dose="5mg", time="08:00"),
                MedicationRecord(name="Vitamin C", dose="500mg", time="18:00"),
            ],
            triggers=[],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="",
            local_foods="",
        )
        queries.save_profile(profile)
        queries.set_onboarding_complete(True)

        queries.ensure_daily_rows(date.today())
        queries.ensure_daily_rows(date.today())

        medications = queries.get_medications_for_date(date.today())
        self.assertEqual(len(medications), 2)
