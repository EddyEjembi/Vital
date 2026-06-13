"""APScheduler — loads jobs from DB and fires notifications."""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db import queries
from vital_types.db import ScheduledJob

from core.app_config import is_demo_mode
from core.notifications import send_notification
from core.tts import speak
from core.weekly_startup import ensure_weekly_report

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

_WEEKLY_REPORT_JOB_ID = "vital_weekly_report"
_WEEKDAY_BY_NAME = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def fire_notification(job_id: str) -> None:
    """Deliver one scheduled notification and optional TTS for a job id."""
    if is_demo_mode():
        logger.info("[scheduler] Demo mode — skipped job %s", job_id)
        return

    jobs = queries.get_combined_scheduler_jobs(date.today())
    job = next((item for item in jobs if item.job_id == job_id), None)
    if job is None:
        logger.warning("[scheduler] Job not found: %s", job_id)
        return

    logger.info(
        "[scheduler] Firing %s (%s) at %s — %s",
        job.job_id,
        job.type,
        job.time or job.interval_minutes,
        job.message[:80],
    )

    result = send_notification("Vitál", job.message, job_id=job.job_id)
    tts_spoken = False
    tts_skip_reason: str | None = "disabled_in_settings"
    if job.tts:
        speak_result = speak(job.message)
        tts_spoken = speak_result.spoken
        tts_skip_reason = speak_result.skipped_reason

    if result.delivered:
        if tts_spoken:
            logger.info("[scheduler] Delivered %s (tts spoken)", job.job_id)
        else:
            logger.info(
                "[scheduler] Delivered %s (tts_enabled=%s, not spoken: %s)",
                job.job_id,
                job.tts,
                tts_skip_reason,
            )
    else:
        logger.info(
            "[scheduler] Notification skipped for %s: %s",
            job.job_id,
            result.skipped_reason,
        )


def _register_job(scheduler: BackgroundScheduler, job: ScheduledJob) -> None:
    """Register one scheduled job with APScheduler."""
    if job.schedule_type == "interval_minutes":
        if job.interval_minutes is None or job.interval_minutes < 1:
            logger.warning("[scheduler] Skipping %s — invalid interval.", job.job_id)
            return
        scheduler.add_job(
            fire_notification,
            trigger=IntervalTrigger(minutes=job.interval_minutes),
            args=[job.job_id],
            id=job.job_id,
            replace_existing=True,
        )
        logger.info(
            "[scheduler] Registered %s every %s min (%s)",
            job.job_id,
            job.interval_minutes,
            job.type,
        )
        return

    if not job.time or ":" not in job.time:
        logger.warning("[scheduler] Skipping %s — missing time.", job.job_id)
        return

    hour_text, minute_text = job.time.split(":", 1)
    scheduler.add_job(
        fire_notification,
        trigger=CronTrigger(hour=int(hour_text), minute=int(minute_text)),
        args=[job.job_id],
        id=job.job_id,
        replace_existing=True,
    )
    logger.info(
        "[scheduler] Registered %s at %s daily (%s)",
        job.job_id,
        job.time,
        job.type,
    )


def _register_weekly_report_job(scheduler: BackgroundScheduler) -> None:
    """Register the Sunday weekly-report generation cron from onboarding settings."""
    structure = queries.get_weekly_check_structure() or {}
    report_day = str(structure.get("report_day", "Sunday"))
    report_time = str(structure.get("report_time", "20:00"))
    day_of_week = _WEEKDAY_BY_NAME.get(report_day.strip().lower(), 6)
    if ":" not in report_time:
        logger.warning("[scheduler] Invalid weekly report time %r — skipping.", report_time)
        return

    hour_text, minute_text = report_time.split(":", 1)
    scheduler.add_job(
        ensure_weekly_report,
        trigger=CronTrigger(
            day_of_week=day_of_week,
            hour=int(hour_text),
            minute=int(minute_text),
        ),
        id=_WEEKLY_REPORT_JOB_ID,
        replace_existing=True,
    )
    logger.info(
        "[scheduler] Registered weekly report on %s at %s.",
        report_day,
        report_time,
    )


def load_jobs_from_db(scheduler: BackgroundScheduler) -> int:
    """Reload active scheduled jobs from the database."""
    job_ids = {job.id for job in scheduler.get_jobs() if job.id is not None}
    jobs = queries.get_combined_scheduler_jobs(date.today())
    active_ids = {job.job_id for job in jobs}
    active_ids.add(_WEEKLY_REPORT_JOB_ID)

    for stale_id in job_ids - active_ids:
        scheduler.remove_job(stale_id)
        logger.info("[scheduler] Removed stale job %s", stale_id)

    for job in jobs:
        _register_job(scheduler, job)
    _register_weekly_report_job(scheduler)
    return len(jobs)


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler and load jobs from the database."""
    global _scheduler

    if is_demo_mode():
        logger.info("[scheduler] Disabled in demo mode.")
        return None

    if _scheduler is not None and _scheduler.running:
        count = load_jobs_from_db(_scheduler)
        logger.info("[scheduler] Reloaded %s active jobs.", count)
        return _scheduler

    _scheduler = BackgroundScheduler()
    count = load_jobs_from_db(_scheduler)
    _scheduler.start()
    logger.info("[scheduler] Started with %s active jobs.", count)
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def reload_scheduler_jobs() -> int:
    """Reload jobs from DB into the running scheduler."""
    if _scheduler is None or not _scheduler.running:
        return 0
    return load_jobs_from_db(_scheduler)
