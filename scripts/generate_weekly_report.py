"""Force-generate the current week's report for local testing.

Usage:
    uv run python scripts/generate_weekly_report.py --seed --fallback-only
    uv run python scripts/generate_weekly_report.py --seed
"""

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import initialize_database
from db import queries
from vital_types.db import ExerciseLogEntry, FoodLogEntry, ProfileInput

from llm.weekly_report import generate_weekly_report


def _seed_week_data() -> None:
    """Insert sample logs for the current week so the report has content."""
    today = date.today()
    week_start = queries.monday_of_week(today)

    if queries.get_profile() is None:
        queries.save_profile(
            ProfileInput(
                name="Test User",
                age=28,
                city="Port Harcourt",
                profession="Engineer",
                goal="Manage wellness",
                conditions=["sickle cell disease"],
                medications=[],
                triggers=["dehydration"],
                wake_time="07:00",
                sleep_time="23:00",
                desk_worker=True,
                exercise_level="light",
                dietary_notes="",
                local_foods="beans, plantain, jollof rice",
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

    queries.upsert_daily_log(today, "pain_level", "2")
    queries.log_water(3, today)
    queries.insert_food_log(
        FoodLogEntry(
            date=today,
            meal_type="lunch",
            food_description="beans and plantain",
            logged_at=datetime.now(timezone.utc),
        )
    )
    queries.insert_exercise_log(
        ExerciseLogEntry(
            date=today,
            exercise_type="walking",
            duration_minutes=20,
            completed=True,
            logged_at=datetime.now(timezone.utc),
        )
    )
    print(f"Seeded sample logs for week starting {week_start.isoformat()}.")


def main() -> None:
    """Run weekly report generation from the command line."""
    parser = argparse.ArgumentParser(description="Generate Vitál weekly report.")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Insert sample week logs before generating.",
    )
    parser.add_argument(
        "--week-start",
        type=str,
        default="",
        help="Monday week_start as YYYY-MM-DD (defaults to current week).",
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="Skip LLM and use the template report (fast local smoke test).",
    )
    args = parser.parse_args()

    initialize_database()

    if args.seed:
        _seed_week_data()

    week_start = (
        date.fromisoformat(args.week_start)
        if args.week_start
        else queries.monday_of_week(date.today())
    )

    print(f"Generating weekly report for {week_start.isoformat()}...")
    report = generate_weekly_report(
        week_start,
        force=True,
        fallback_only=args.fallback_only,
    )
    if report is None:
        print("Weekly report generation failed.")
        raise SystemExit(1)

    print(f"Weekly report saved for {report.week_start.isoformat()}.")
    print("---")
    print(report.report_text[:1200])
    if len(report.report_text) > 1200:
        print("...")


if __name__ == "__main__":
    main()
