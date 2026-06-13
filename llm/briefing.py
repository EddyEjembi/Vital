"""Morning briefing generation for the dashboard."""

from datetime import date, datetime

from db import queries
from vital_types.db import ProfileInput

from llm.client import get_llm_client
from llm.onboarding import MORNING_BRIEFING_SYSTEM, build_morning_briefing_prompt


def _period_of_day(now: datetime) -> str:
    """Return morning, afternoon, or evening for the current hour."""
    if now.hour < 12:
        return "morning"
    if now.hour < 17:
        return "afternoon"
    return "evening"


def _format_plan_job(job_time: str, job_type: str, message: str, extra: str) -> str:
    """Format one scheduled job line for the briefing context."""
    suffix = f" ({extra})" if extra else ""
    return f"- {job_time} [{job_type}] {message}{suffix}"


def build_briefing_context(profile: ProfileInput) -> str:
    """Assemble context lines for the morning briefing prompt.

    Includes the actual scheduled meal/exercise jobs so the briefing
    describes the same plan shown on the Nutrition and Movement tabs.
    """
    today = date.today()
    logs = queries.get_daily_logs_for_date(today)
    medications = queries.get_medications_for_date(today)
    daily_plan = queries.get_daily_plan(today)
    now = datetime.now()

    log_lines = [f"- {entry.field_id}: {entry.value}" for entry in logs]
    med_lines = [
        f"- {med.medication_name} at {med.scheduled_time}: "
        f"{'taken' if med.taken else 'pending'}"
        for med in medications
    ]

    plan_block = "  (not generated yet)"
    job_lines: list[str] = []
    if daily_plan is not None:
        plan_block = daily_plan.summary
        for job in sorted(daily_plan.jobs, key=lambda item: item.time):
            extra = ""
            if job.volume_ml:
                extra = f"{job.volume_ml}ml"
            if job.exercise_type:
                extra = f"{job.exercise_type}, {job.duration_minutes} min"
            job_lines.append(_format_plan_job(job.time, job.type, job.message, extra))
    jobs_block = "\n".join(job_lines) if job_lines else "  (none scheduled)"

    return (
        f"Date: {today.isoformat()} ({_period_of_day(now)})\n"
        f"Today's schedule summary:\n{plan_block}\n"
        f"Today's exact scheduled plan (meals, exercise, hydration, meds):\n{jobs_block}\n"
        f"Today's logs:\n" + ("\n".join(log_lines) if log_lines else "  (none yet)") + "\n"
        f"Medications today:\n" + ("\n".join(med_lines) if med_lines else "  (none scheduled)")
    )


def generate_morning_briefing(profile: ProfileInput | None = None) -> str:
    """Generate a short morning briefing via the LLM."""
    resolved_profile = profile or queries.get_profile()
    if resolved_profile is None:
        return "Welcome back to Vitál. Let's make today a good one."

    cached = queries.get_morning_briefing_cache()
    if cached:
        return cached

    client = get_llm_client()
    context = build_briefing_context(resolved_profile)
    prompt = build_morning_briefing_prompt(resolved_profile, context)
    briefing = client.chat(
        prompt,
        extra_messages=None,
        use_tools=False,
    )
    trimmed = briefing.strip()
    if not trimmed:
        trimmed = f"Good {_period_of_day(datetime.now())}, {resolved_profile.name}. Ready for today?"
    queries.save_morning_briefing_cache(date.today(), trimmed)
    return trimmed
