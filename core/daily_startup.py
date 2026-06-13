"""Daily startup — seed rows, generate today's schedule, reload scheduler."""

import logging
from datetime import date, datetime

from db import queries
from llm.daily_schedule import generate_daily_plan, is_past_wake_time, should_generate_daily_plan

from core.scheduler import reload_scheduler_jobs
from core.weekly_startup import run_weekly_startup

logger = logging.getLogger(__name__)


def ensure_today_schedule(force: bool = False) -> bool:
    """Generate today's schedule when needed and reload scheduler jobs."""
    today = date.today()
    profile = queries.get_profile()
    if profile is None or not queries.check_onboarding_status():
        return False

    if force and queries.has_daily_plan(today):
        queries.delete_daily_plan(today)
        logger.info("Cleared existing daily plan for %s (force regenerate).", today.isoformat())

    if not force and not should_generate_daily_plan(today):
        if queries.has_daily_plan(today):
            logger.info("Today's schedule already loaded (%s).", today.isoformat())
        elif not is_past_wake_time(profile):
            logger.info(
                "Before wake time (%s) — daily schedule will generate after %s.",
                datetime.now().strftime("%H:%M"),
                profile.wake_time,
            )
        if queries.has_daily_plan(today):
            reload_scheduler_jobs()
        return queries.has_daily_plan(today)

    plan = generate_daily_plan(today)
    # A new plan invalidates any cached briefing so the Home tab matches it.
    queries.clear_morning_briefing_cache()
    job_count = reload_scheduler_jobs()
    logger.info(
        "Daily schedule ready for %s: %s timed jobs, %s registered in scheduler.",
        today.isoformat(),
        len(plan.jobs),
        job_count,
    )
    return True


def run_daily_startup(force_schedule: bool = False) -> None:
    """Run idempotent daily startup tasks for onboarded users."""
    if not queries.check_onboarding_status():
        return

    queries.ensure_daily_rows()
    ensure_today_schedule(force=force_schedule)
    run_weekly_startup()
