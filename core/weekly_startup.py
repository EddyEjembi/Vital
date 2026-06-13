"""Weekly report scheduling — Sunday cron + startup catch-up."""

import logging
from datetime import date, datetime, time, timedelta

from db import queries

from llm.weekly_report import generate_weekly_report

logger = logging.getLogger(__name__)

_DEFAULT_REPORT_DAY = "Sunday"
_DEFAULT_REPORT_TIME = "20:00"

_WEEKDAY_BY_NAME = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _parse_report_time(raw_time: str) -> time:
    """Parse HH:MM report time from weekly_check_structure."""
    try:
        hours_text, minutes_text = raw_time.strip().split(":", 1)
        return time(hour=int(hours_text), minute=int(minutes_text))
    except (AttributeError, TypeError, ValueError):
        return time(hour=20, minute=0)


def _report_date_in_week(week_start: date, report_day: str) -> date:
    """Return the calendar date of report_day within the Monday-based week."""
    target_weekday = _WEEKDAY_BY_NAME.get(report_day.strip().lower(), 6)
    for offset in range(7):
        candidate = week_start + timedelta(days=offset)
        if candidate.weekday() == target_weekday:
            return candidate
    return week_start + timedelta(days=6)


def _report_deadline_for_week(week_start: date) -> datetime:
    """Return the local datetime when a week's report becomes due."""
    structure = queries.get_weekly_check_structure() or {}
    report_day = str(structure.get("report_day", _DEFAULT_REPORT_DAY))
    report_time = str(structure.get("report_time", _DEFAULT_REPORT_TIME))
    report_date = _report_date_in_week(week_start, report_day)
    parsed_time = _parse_report_time(report_time)
    return datetime.combine(report_date, parsed_time)


def is_weekly_report_due(week_start: date, now: datetime | None = None) -> bool:
    """Return true when the report deadline for week_start has passed."""
    current = now or datetime.now()
    return current >= _report_deadline_for_week(week_start)


def pending_weekly_report_start(now: datetime | None = None) -> date | None:
    """Return the Monday week_start that needs a report, or None."""
    if not queries.check_onboarding_status():
        return None

    current = now or datetime.now()
    today = current.date()
    this_week = queries.monday_of_week(today)
    last_week = this_week - timedelta(days=7)

    for week_start in (last_week, this_week):
        if not is_weekly_report_due(week_start, current):
            continue
        if queries.get_weekly_report(week_start) is not None:
            continue
        return week_start
    return None


def ensure_weekly_report(force: bool = False, now: datetime | None = None) -> bool:
    """Generate the pending weekly report when due; return True if one exists."""
    current = now or datetime.now()
    week_start = queries.monday_of_week(current.date()) if force else pending_weekly_report_start(current)
    if week_start is None and not force:
        return queries.get_weekly_report(queries.monday_of_week(current.date())) is not None

    if week_start is None:
        week_start = queries.monday_of_week(current.date())

    report = generate_weekly_report(week_start, force=force)
    if report is None:
        return False

    logger.info(
        "[weekly_report] Ready for week %s (%s chars).",
        week_start.isoformat(),
        len(report.report_text),
    )
    return True


def run_weekly_startup(force: bool = False) -> None:
    """Run idempotent weekly startup tasks for onboarded users."""
    if not queries.check_onboarding_status():
        return
    ensure_weekly_report(force=force)
