"""Clear all daily plan and schedule rows for a fresh start."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import queries
from db.database import initialize_database


def main() -> None:
    """Delete every daily plan and schedule job from the database."""
    initialize_database()
    removed = queries.clear_all_daily_schedules()
    print(f"Cleared {removed} daily schedule row(s) from daily_plans + daily_schedule_jobs.")


if __name__ == "__main__":
    main()
