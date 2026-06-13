"""Weekly report generation — LLM prompt, schema, validation, and persistence."""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from db import queries
from vital_types.db import ProfileInput, WeeklyReport

from llm.client import get_llm_client

logger = logging.getLogger(__name__)

WEEKLY_REPORT_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "report_text": {
            "type": "string",
            "minLength": 50,
            "maxLength": 4000,
            "description": (
                "A warm weekly coaching narrative summarizing wins, gaps, and "
                "one focus for next week. Plain text, 3-6 short paragraphs."
            ),
        },
        "highlights": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 5, "maxLength": 200},
            "description": "Bullet-worthy wins from the week.",
        },
        "focus_next_week": {
            "type": "string",
            "minLength": 10,
            "maxLength": 300,
            "description": "One practical focus area for the coming week.",
        },
    },
    "required": ["report_text", "highlights", "focus_next_week"],
    "additionalProperties": False,
}

WEEKLY_REPORT_SYSTEM = """
You are Vitál's weekly wellness coach. You write an end-of-week report for ONE user based on
their profile and the structured week data provided. You are NOT having a conversation — you
output a single JSON object the app saves to the Report tab.

WHAT YOU RECEIVE:
- User profile (goal, conditions, medications, dietary context).
- Aggregated stats for the week (medication adherence, exercise count, food logs, check-ins).
- Day-by-day check-in logs, food logs, and exercise logs when available.

RULES:
- Be encouraging, specific, and honest — cite real numbers from the data.
- Mention medication adherence, movement, hydration/check-ins, and meals logged when relevant.
- If data is sparse, say so kindly and suggest one concrete habit to build.
- Never diagnose. For serious symptoms mentioned in logs, advise seeking medical care.
- report_text: 3-6 short paragraphs, readable on the Report tab.
- highlights: 1-5 short win bullets drawn from the data.
- focus_next_week: one actionable priority (not a full meal plan — daily plans handle that).

Return ONLY valid JSON matching the schema. No markdown fences, no commentary.
"""


class WeeklyReportValidationError(ValueError):
    """Raised when weekly report JSON fails validation."""


def _week_label(week_start: date) -> str:
    """Format a week range label for prompts."""
    week_end = week_start + timedelta(days=6)
    return f"{week_start.isoformat()} to {week_end.isoformat()}"


def build_weekly_report_context(
    profile: ProfileInput,
    week_start: date,
) -> str:
    """Assemble week data for the weekly report LLM prompt."""
    week_end = week_start + timedelta(days=6)
    summary = queries.get_weekly_summary_for_week(week_start)

    check_in_lines: list[str] = []
    food_lines: list[str] = []
    exercise_lines: list[str] = []

    current = week_start
    while current <= week_end:
        day_label = current.strftime("%A %Y-%m-%d")
        for entry in queries.get_daily_logs_for_date(current):
            check_in_lines.append(f"  {day_label}: {entry.field_id}={entry.value}")
        for entry in queries.get_food_logs_for_date(current):
            food_lines.append(
                f"  {day_label}: {entry.meal_type} — {entry.food_description}"
            )
        for entry in queries.get_exercise_logs_for_date(current):
            if entry.completed:
                exercise_lines.append(
                    f"  {day_label}: {entry.exercise_type} {entry.duration_minutes} min"
                )
        current += timedelta(days=1)

    return (
        f"Week: {_week_label(week_start)}\n"
        f"User: {profile.name} | Goal: {profile.goal}\n"
        f"Conditions: {', '.join(profile.conditions) or 'none'}\n"
        f"Summary stats: {json.dumps(summary)}\n"
        f"Check-in logs:\n"
        + ("\n".join(check_in_lines) if check_in_lines else "  (none)")
        + "\nFood logs:\n"
        + ("\n".join(food_lines) if food_lines else "  (none)")
        + "\nExercise logs:\n"
        + ("\n".join(exercise_lines) if exercise_lines else "  (none)")
    )


def build_weekly_report_user_prompt(
    profile: ProfileInput,
    week_start: date,
) -> str:
    """Build the user prompt for weekly report generation."""
    context = build_weekly_report_context(profile, week_start)
    return (
        f"Write the weekly wellness report for {profile.name}.\n\n"
        f"{context}\n\n"
        "Return JSON with report_text, highlights, and focus_next_week."
    )


def validate_weekly_report_response(payload: dict[str, object]) -> str:
    """Validate LLM weekly report JSON and return the narrative text."""
    report_text = payload.get("report_text")
    highlights = payload.get("highlights")
    focus = payload.get("focus_next_week")

    if not isinstance(report_text, str) or not report_text.strip():
        raise WeeklyReportValidationError("report_text is required.")
    if not isinstance(highlights, list) or len(highlights) < 1:
        raise WeeklyReportValidationError("highlights must be a non-empty array.")
    if not isinstance(focus, str) or not focus.strip():
        raise WeeklyReportValidationError("focus_next_week is required.")

    trimmed = report_text.strip()
    highlight_block = "\n".join(
        f"- {item.strip()}"
        for item in highlights
        if isinstance(item, str) and item.strip()
    )
    if highlight_block:
        trimmed = f"{trimmed}\n\n**Highlights**\n{highlight_block}"
    trimmed = f"{trimmed}\n\n**Focus next week:** {focus.strip()}"
    return trimmed


def _fallback_weekly_report_text(
    profile: ProfileInput,
    week_start: date,
) -> str:
    """Build a deterministic report when the LLM is unavailable."""
    summary = queries.get_weekly_summary_for_week(week_start)
    return (
        f"Week of {week_start.isoformat()}: {profile.name}, here's your week in numbers. "
        f"You took {summary.get('medications_taken')}/{summary.get('medications_total')} "
        f"scheduled medications ({summary.get('medication_adherence_percent')}% adherence), "
        f"completed {summary.get('exercises_completed')} exercises, and logged "
        f"{summary.get('food_entries')} meals. "
        f"Keep building steady habits — Vitál will keep nudging you daily."
    )


def generate_weekly_report(
    week_start: date,
    force: bool = False,
    fallback_only: bool = False,
) -> WeeklyReport | None:
    """Generate and persist the weekly report for a Monday week_start date."""
    profile = queries.get_profile()
    if profile is None or not queries.check_onboarding_status():
        logger.info("[weekly_report] Skipped — user not onboarded.")
        return None

    existing = queries.get_weekly_report(week_start)
    if existing is not None and not force:
        logger.info(
            "[weekly_report] Report already exists for %s — loading.",
            week_start.isoformat(),
        )
        return existing
    if existing is not None and force:
        queries.delete_weekly_report(week_start)
        logger.info(
            "[weekly_report] Cleared existing report for %s (force regenerate).",
            week_start.isoformat(),
        )

    summary = queries.get_weekly_summary_for_week(week_start)
    report_text = ""

    if fallback_only:
        report_text = _fallback_weekly_report_text(profile, week_start)
        logger.info(
            "[weekly_report] Template report for %s (fallback_only).",
            week_start.isoformat(),
        )
    else:
        try:
            client = get_llm_client()
            payload = client.generate_onboarding_json(
                build_weekly_report_user_prompt(profile, week_start),
                system_prompt=WEEKLY_REPORT_SYSTEM,
                json_schema=WEEKLY_REPORT_JSON_SCHEMA,
            )
            report_text = validate_weekly_report_response(payload)
            logger.info(
                "[weekly_report] LLM report for %s (%s chars).",
                week_start.isoformat(),
                len(report_text),
            )
        except Exception as error:
            logger.warning(
                "[weekly_report] LLM failed for %s: %s — using template fallback.",
                week_start.isoformat(),
                error,
            )
            report_text = _fallback_weekly_report_text(profile, week_start)

    report = WeeklyReport(
        week_start=week_start,
        report_text=report_text,
        water_goals_hit=0,
        medication_adherence=float(summary.get("medication_adherence_percent", 0.0)),
        exercises_completed=int(summary.get("exercises_completed", 0)),
        generated_at=datetime.now(timezone.utc),
    )
    queries.insert_weekly_report(report)
    return report
