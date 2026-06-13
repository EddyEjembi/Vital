"""Daily spoken greeting — once per calendar day on first app open."""

import logging
from datetime import date, datetime

from db import queries
from vital_types.db import ProfileInput

from core.app_config import is_demo_mode
from core.tts import speak
from llm.briefing import generate_morning_briefing

logger = logging.getLogger(__name__)

_MAX_SPOKEN_GREETING_CHARS = 280


def period_of_day(now: datetime | None = None) -> str:
    """Return morning, afternoon, or evening for the current hour."""
    current = now or datetime.now()
    if current.hour < 12:
        return "morning"
    if current.hour < 17:
        return "afternoon"
    return "evening"


def build_spoken_greeting_text(profile: ProfileInput, briefing: str) -> str:
    """Build a time-aware spoken greeting with today's plan highlights."""
    now = datetime.now()
    period = period_of_day(now)
    opener = f"Good {period}, {profile.name}. Welcome back to Vitál."

    plan = queries.get_daily_plan(date.today())
    plan_hook = ""
    if plan is not None:
        hydration_jobs = [job for job in plan.jobs if job.type == "hydration"]
        exercise_job = next((job for job in plan.jobs if job.type == "exercise"), None)
        plan_hook = (
            f" Today we're aiming for {plan.hydration_goal_liters} litres of water"
            f" across {len(hydration_jobs)} reminders."
        )
        if exercise_job is not None:
            exercise_label = exercise_job.exercise_type or "movement"
            plan_hook += (
                f" Your exercise is {exercise_label} at {exercise_job.time}"
                f" for {exercise_job.duration_minutes or 20} minutes."
            )

    briefing_snippet = briefing.strip()
    if briefing_snippet:
        first_sentence = briefing_snippet.split(".")[0].strip()
        if first_sentence and first_sentence[-1] != ".":
            first_sentence = f"{first_sentence}."
        coach_line = f" {first_sentence}"
    else:
        coach_line = " Let's make today a good one."

    spoken = f"{opener}{plan_hook}{coach_line}"
    if len(spoken) > _MAX_SPOKEN_GREETING_CHARS:
        spoken = spoken[: _MAX_SPOKEN_GREETING_CHARS - 3].rstrip() + "..."
    return spoken


def deliver_daily_greeting_if_needed() -> bool:
    """Speak the daily greeting once per calendar day; return True if spoken."""
    if is_demo_mode():
        logger.info("[greeting] Demo mode — skipping TTS.")
        return False

    if queries.already_greeted_today():
        logger.info("[greeting] Already delivered today — skipping TTS.")
        return False

    profile = queries.get_profile()
    if profile is None:
        return False

    briefing = generate_morning_briefing(profile)
    spoken_text = build_spoken_greeting_text(profile, briefing)

    result = speak(spoken_text, allow_long_text=True)
    if result.spoken:
        queries.mark_greeted_today()
        logger.info("[greeting] Spoke daily greeting for %s.", profile.name)
    else:
        logger.info("[greeting] TTS skipped for %s: %s", profile.name, result.skipped_reason)
    return result.spoken
