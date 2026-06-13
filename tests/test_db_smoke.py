"""Smoke tests for the Vitál database layer."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from db.database import close_connection, get_connection, initialize_database, reset_database, set_db_path
from db import queries
from vital_types.db import (
    DailyLogSchemaField,
    MedicationLogEntry,
    MedicationRecord,
    ProfileInput,
    ScheduledJob,
)


class DatabaseSmokeTest(unittest.TestCase):
    """Verify database initialisation and core profile read/write."""

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

    def test_initialize_creates_tables(self) -> None:
        """All expected tables should exist after initialisation."""
        connection = get_connection()
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name ASC;"
        ).fetchall()
        table_names = {row["name"] for row in rows}
        expected_tables = {
            "profile",
            "daily_log_schema",
            "daily_logs",
            "medication_log",
            "food_log",
            "exercise_log",
            "scheduled_jobs",
            "meal_plan",
            "weekly_reports",
            "notifications_log",
            "presence_log",
            "settings",
        }
        self.assertTrue(expected_tables.issubset(table_names))

    def test_save_and_get_profile(self) -> None:
        """A saved profile should round-trip through the database."""
        profile_input = ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
            conditions=["sickle cell disease"],
            medications=[
                MedicationRecord(name="Folic acid", dose="5mg", time="08:00"),
            ],
            triggers=["dehydration", "cold temperatures"],
            wake_time="07:00",
            sleep_time="23:00",
            desk_worker=True,
            exercise_level="light",
            dietary_notes="No processed sugar",
            local_foods="eba, egusi soup, beans",
        )

        profile_id = queries.save_profile(profile_input)
        self.assertGreater(profile_id, 0)

        profile = queries.get_profile()
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.name, "Amara")
        self.assertEqual(profile.age, 24)
        self.assertEqual(profile.city, "Port Harcourt")
        self.assertEqual(profile.conditions, ["sickle cell disease"])
        self.assertEqual(len(profile.medications), 1)
        self.assertEqual(profile.medications[0].name, "Folic acid")
        self.assertTrue(profile.desk_worker)

    def test_onboarding_status_requires_flag(self) -> None:
        """Onboarding is incomplete until the completion flag is set."""
        profile_input = ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
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
        queries.save_profile(profile_input)
        self.assertFalse(queries.check_onboarding_status())

        queries.set_onboarding_complete(True)
        self.assertTrue(queries.check_onboarding_status())

    def test_daily_log_schema_round_trip(self) -> None:
        """Dynamic check-in fields should persist and load in order."""
        fields = [
            DailyLogSchemaField(
                field_id="pain_level",
                label="Pain level today",
                type="scale_1_10",
                display_order=1,
                reason="SCD monitoring",
            ),
            DailyLogSchemaField(
                field_id="water_cups",
                label="Water cups today",
                type="number",
                display_order=2,
                reason="Hydration tracking",
            ),
        ]
        queries.replace_daily_log_schema(fields)
        loaded_fields = queries.get_daily_log_schema()
        self.assertEqual(len(loaded_fields), 2)
        self.assertEqual(loaded_fields[0].field_id, "pain_level")
        self.assertEqual(loaded_fields[1].field_id, "water_cups")

    def test_daily_log_upsert_and_read(self) -> None:
        """Daily log values should upsert by date and field id."""
        today = date.today()
        queries.upsert_daily_log(today, "pain_level", "4")
        queries.upsert_daily_log(today, "pain_level", "6")

        entries = queries.get_daily_logs_for_date(today)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].field_id, "pain_level")
        self.assertEqual(entries[0].value, "6")

    def test_scheduled_jobs_round_trip(self) -> None:
        """Scheduled jobs should persist and reload."""
        jobs = [
            ScheduledJob(
                job_id="folic_acid_morning",
                type="medication",
                schedule_type="daily_time",
                time="08:00",
                days="daily",
                message="Time for your folic acid (5mg)",
                tts=True,
                active=True,
                context="Daily supplementation",
            ),
        ]
        queries.replace_scheduled_jobs(jobs)
        loaded_jobs = queries.get_all_scheduled_jobs()
        self.assertEqual(len(loaded_jobs), 1)
        self.assertEqual(loaded_jobs[0].job_id, "folic_acid_morning")
        self.assertTrue(loaded_jobs[0].tts)

    def test_medication_log_mark_taken(self) -> None:
        """Medication doses should be markable as taken."""
        today = date.today()
        queries.insert_medication_log(
            MedicationLogEntry(
                date=today,
                medication_name="Folic acid",
                dose="5mg",
                scheduled_time="08:00",
                taken=False,
                taken_at=None,
            )
        )
        updated = queries.mark_medication_taken(today, "Folic acid", "08:00")
        self.assertTrue(updated)

        medications = queries.get_medications_for_date(today)
        self.assertEqual(len(medications), 1)
        self.assertTrue(medications[0].taken)
        self.assertIsNotNone(medications[0].taken_at)

    def test_reset_database_clears_data(self) -> None:
        """Reset should clear rows while keeping the schema intact."""
        profile_input = ProfileInput(
            name="Amara",
            age=24,
            city="Port Harcourt",
            profession="Student",
            goal="Manage a health condition",
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
        queries.save_profile(profile_input)
        reset_database()
        self.assertIsNone(queries.get_profile())


if __name__ == "__main__":
    unittest.main()
