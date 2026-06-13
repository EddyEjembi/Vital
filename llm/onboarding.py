"""Onboarding prompts, validation, and plan parsing for Call 1 and Call 2."""

import json
import re
from typing import cast

from vital_types.db import (
    DailyLogFieldType,
    DailyLogSchemaField,
    PresenceConfig,
    ProfileInput,
    ScheduledJob,
    ScheduledJobType,
    ScheduleType,
)
from core.personalization import personalize_message
from vital_types.onboarding import (
    ExercisePlan,
    FollowUpQuestion,
    MealPlanFramework,
    OnboardingCommitData,
    OnboardingPlan,
    WeeklyCheckStructure,
)

_PLACEHOLDER_PHRASES = (
    "why track this",
    "coach instructions for this user",
    "nutrition guidance",
    "safety notes",
    "why this reminder matters",
    "<why this field matters",
    "<short reminder",
    "<personalised",
    "<coach behaviour",
)

_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_VALID_FIELD_TYPES = {"scale_1_10", "number", "select", "boolean", "text"}
_VALID_FOLLOW_UP_TYPES = {"number", "text"}
_VALID_JOB_TYPES = {"medication", "hydration", "exercise", "break", "meal", "check_in"}
_MAX_FOLLOW_UP_QUESTIONS = 3

CALL_1_SYSTEM = (
    "You are Vitál's onboarding interviewer. Given a new user's profile, return up to 3 "
    "adaptive follow-up questions that will help design their personal wellness plan. "
    "Return ONLY valid JSON. No markdown. No extra keys."
)

CALL_2_SYSTEM = (
    "You are Vitál's plan designer. Given a user profile and their follow-up answers, "
    "design a complete personal wellness plan as JSON. The plan must be practical, "
    "safe, and tailored to this specific user — use their name, conditions, triggers, "
    "medications, profession, and local foods. Never copy schema examples or placeholder "
    "text verbatim. Never add medication reminders unless the profile lists medications. "
    "Never diagnose. Return ONLY valid JSON. No markdown. No extra keys."
)

MORNING_BRIEFING_SYSTEM = (
    "You are Vitál, a warm wellness coach. Write a short daily briefing (2-4 sentences) "
    "for the user based on their profile, today's schedule, and context. Be encouraging, "
    "personal, and practical — like a coach setting them up for the day. "
    "CRITICAL: when you mention meals, exercise, or timings, use ONLY the exact items "
    "listed under 'Today's exact scheduled plan' in the context. Never invent meals, "
    "workouts, or times that are not in that list — the user sees the same plan on "
    "other tabs and it must match. Do not mention tools or JSON."
)

_TIME_JSON_PATTERN = "^([01][0-9]|2[0-3]):[0-5][0-9]$"

FOLLOW_UP_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "follow_up_questions": {
            "type": "array",
            "maxItems": _MAX_FOLLOW_UP_QUESTIONS,
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "question": {"type": "string", "minLength": 5, "maxLength": 200},
                    "type": {"type": "string", "enum": ["number", "text"]},
                    "reason": {"type": "string", "maxLength": 200},
                },
                "required": ["question_id", "question", "type", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["follow_up_questions"],
    "additionalProperties": False,
}

ONBOARDING_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "daily_log_fields": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "label": {"type": "string", "minLength": 2, "maxLength": 100},
                    "type": {
                        "type": "string",
                        "enum": ["scale_1_10", "number", "select", "boolean", "text"],
                    },
                    "options": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string", "maxLength": 250},
                },
                "required": ["field_id", "label", "type", "options", "reason"],
                "additionalProperties": False,
            },
        },
        "scheduled_jobs": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "medication",
                            "hydration",
                            "exercise",
                            "break",
                            "meal",
                            "check_in",
                        ],
                    },
                    "time": {"type": "string", "pattern": _TIME_JSON_PATTERN},
                    "days": {"type": "string"},
                    "message": {"type": "string", "minLength": 5, "maxLength": 200},
                    "tts": {"type": "boolean"},
                    "context": {"type": "string", "maxLength": 250},
                },
                "required": ["job_id", "type", "time", "days", "message", "tts", "context"],
                "additionalProperties": False,
            },
        },
        "meal_plan_framework": {
            "type": "object",
            "properties": {
                "nutrients_to_prioritise": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "nutrients_to_moderate": {"type": "array", "items": {"type": "string"}},
                "meal_frequency": {"type": "integer", "minimum": 1, "maximum": 8},
                "notes": {"type": "string", "maxLength": 500},
            },
            "required": [
                "nutrients_to_prioritise",
                "nutrients_to_moderate",
                "meal_frequency",
                "notes",
            ],
            "additionalProperties": False,
        },
        "exercise_plan": {
            "type": "object",
            "properties": {
                "frequency": {"type": "string"},
                "intensity": {"type": "string"},
                "session_duration_minutes": {"type": "integer", "minimum": 5, "maximum": 120},
                "types": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "avoid": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string", "maxLength": 500},
            },
            "required": [
                "frequency",
                "intensity",
                "session_duration_minutes",
                "types",
                "avoid",
                "notes",
            ],
            "additionalProperties": False,
        },
        "weekly_check_structure": {
            "type": "object",
            "properties": {
                "report_day": {"type": "string"},
                "report_time": {"type": "string", "pattern": _TIME_JSON_PATTERN},
                "replan_day": {"type": "string"},
                "replan_time": {"type": "string", "pattern": _TIME_JSON_PATTERN},
            },
            "required": ["report_day", "report_time", "replan_day", "replan_time"],
            "additionalProperties": False,
        },
        "presence_check": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "max_continuous_minutes": {"type": "integer", "minimum": 5, "maximum": 240},
                "break_message": {"type": "string", "minLength": 5, "maxLength": 200},
                "break_duration_minutes": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": [
                "enabled",
                "max_continuous_minutes",
                "break_message",
                "break_duration_minutes",
            ],
            "additionalProperties": False,
        },
        "hydration_goal_liters": {"type": "number", "minimum": 0.5, "maximum": 8},
        "system_prompt_additions": {"type": "string", "maxLength": 1000},
        "coach_quick_questions": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 60},
        },
    },
    "required": [
        "daily_log_fields",
        "scheduled_jobs",
        "meal_plan_framework",
        "exercise_plan",
        "weekly_check_structure",
        "presence_check",
        "hydration_goal_liters",
        "system_prompt_additions",
        "coach_quick_questions",
    ],
    "additionalProperties": False,
}


class OnboardingValidationError(Exception):
    """Raised when onboarding LLM output fails schema validation."""


def profile_to_prompt_dict(profile: ProfileInput) -> dict[str, object]:
    """Serialize a profile for onboarding LLM prompts."""
    return {
        "name": profile.name,
        "age": profile.age,
        "city": profile.city,
        "profession": profile.profession,
        "goal": profile.goal,
        "conditions": profile.conditions,
        "medications": [
            {"name": item.name, "dose": item.dose, "time": item.time}
            for item in profile.medications
        ],
        "triggers": profile.triggers,
        "wake_time": profile.wake_time,
        "sleep_time": profile.sleep_time,
        "desk_worker": profile.desk_worker,
        "exercise_level": profile.exercise_level,
        "dietary_notes": profile.dietary_notes,
        "local_foods": profile.local_foods,
    }


def build_call_1_user_prompt(profile: ProfileInput) -> str:
    """Build the user prompt for onboarding Call 1."""
    profile_json = json.dumps(profile_to_prompt_dict(profile), indent=2)
    return f"""
        User profile:
        {profile_json}

        Return JSON with this exact shape (max {_MAX_FOLLOW_UP_QUESTIONS} questions):
        {{
        "follow_up_questions": [
            {{
            "question_id": "snake_case_id",
            "question": "question text for the user",
            "type": "number",
            "reason": "why this question matters"
            }}
        ]
        }}

        Use type as one of: number, text. Return an empty array if no follow-ups are needed.
        """


def build_call_2_user_prompt(
    profile: ProfileInput,
    follow_up_answers: dict[str, str],
    additional_notes: str,
) -> str:
    """Build the user prompt for onboarding Call 2."""
    payload = {
        "profile": profile_to_prompt_dict(profile),
        "follow_up_answers": follow_up_answers,
        "additional_notes": additional_notes.strip() or None,
    }
    payload_json = json.dumps(payload, indent=2)
    return f"""
        User data:
        {payload_json}

        Return JSON with this exact top-level shape (replace ALL angle-bracket placeholders with real, user-specific content):
        {{
        "daily_log_fields": [
            {{
            "field_id": "<snake_case_id>",
            "label": "<human-readable label>",
            "type": "<scale_1_10|number|select|boolean|text>",
            "options": [],
            "reason": "<why this field matters for THIS user>"
            }}
        ],
        "scheduled_jobs": [
            {{
            "job_id": "<unique_snake_case_id>",
            "type": "<medication|hydration|exercise|break|meal|check_in>",
            "time": "HH:MM",
            "days": "daily",
            "message": "<short reminder the coach will speak aloud>",
            "tts": true,
            "context": "<why this reminder matters for THIS user>"
            }}
        ],
        "meal_plan_framework": {{
            "nutrients_to_prioritise": ["<nutrient>"],
            "nutrients_to_moderate": ["<nutrient>"],
            "meal_frequency": 3,
            "notes": "<personalised nutrition notes using their conditions and local foods>"
        }},
        "exercise_plan": {{
            "frequency": "<daily|weekly>",
            "intensity": "<low|moderate>",
            "session_duration_minutes": 20,
            "types": ["<activity>"],
            "avoid": ["<activity or trigger to avoid>"],
            "notes": "<personalised safety notes for THIS user>"
        }},
        "weekly_check_structure": {{
            "report_day": "Sunday",
            "report_time": "20:00",
            "replan_day": "Sunday",
            "replan_time": "20:30"
        }},
        "presence_check": {{
            "enabled": true,
            "max_continuous_minutes": 30,
            "break_message": "<short desk-break nudge — do NOT include their name here>",
            "break_duration_minutes": 5
        }},
        "hydration_goal_liters": 2.5,
        "system_prompt_additions": "<coach behaviour instructions tailored to THIS user>",
        "coach_quick_questions": ["<chip label>", "<chip label>"]
        }}

        Rules:
        - daily_log_fields.type must be one of: scale_1_10, number, select, boolean, text
        - scheduled_jobs.type must be one of: medication, hydration, exercise, break, meal, check_in
        - scheduled_jobs.time must be HH:MM for timed reminders
        - scheduled_jobs: medication reminders ONLY (one per profile.medications entry). Empty array if no meds.
        - hydration_goal_liters: daily water target in litres (e.g. 2.5–6.0 based on conditions and follow-ups)
        - Do NOT put hydration or exercise times in scheduled_jobs — those are generated fresh each morning
        - Every reason, note, message, and system_prompt_additions must be specific — never echo placeholders
        - coach_quick_questions: 4 to 6 short chip labels for the coach tab
        """


def build_morning_briefing_prompt(profile: ProfileInput, briefing_context: str) -> str:
    """Build a user prompt for the daily briefing shown on the dashboard."""
    return (
        f"User: {profile.name}, goal: {profile.goal}, city: {profile.city}.\n"
        f"Context:\n{briefing_context}\n\n"
        "Write today's coaching briefing — upbeat, specific to their plan today."
    )


def validate_follow_up_response(payload: dict[str, object]) -> list[FollowUpQuestion]:
    """Validate Call 1 JSON and return parsed follow-up questions."""
    raw_questions = payload.get("follow_up_questions")
    if raw_questions is None:
        raise OnboardingValidationError("Missing follow_up_questions array.")
    if not isinstance(raw_questions, list):
        raise OnboardingValidationError("follow_up_questions must be an array.")

    questions: list[FollowUpQuestion] = []
    for index, item in enumerate(raw_questions[:_MAX_FOLLOW_UP_QUESTIONS]):
        if not isinstance(item, dict):
            raise OnboardingValidationError(f"follow_up_questions[{index}] must be an object.")
        question_id = item.get("question_id")
        question_text = item.get("question")
        question_type = item.get("type")
        reason = item.get("reason")
        if not isinstance(question_id, str) or not question_id.strip():
            raise OnboardingValidationError(f"follow_up_questions[{index}].question_id is required.")
        if not isinstance(question_text, str) or not question_text.strip():
            raise OnboardingValidationError(f"follow_up_questions[{index}].question is required.")
        if not isinstance(question_type, str) or question_type not in _VALID_FOLLOW_UP_TYPES:
            raise OnboardingValidationError(f"follow_up_questions[{index}].type is invalid.")
        if not isinstance(reason, str):
            reason = ""
        questions.append(
            FollowUpQuestion(
                question_id=question_id.strip(),
                question=question_text.strip(),
                type=question_type,
                reason=reason.strip(),
            )
        )
    return questions


def _require_string_field(data: dict[str, object], key: str, label: str) -> str:
    """Require a non-empty string field on a dict."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OnboardingValidationError(f"{label}.{key} must be a non-empty string.")
    return value.strip()


def _parse_daily_log_fields(raw_fields: object) -> list[DailyLogSchemaField]:
    """Parse daily_log_fields from Call 2."""
    if not isinstance(raw_fields, list) or len(raw_fields) == 0:
        raise OnboardingValidationError("daily_log_fields must be a non-empty array.")

    fields: list[DailyLogSchemaField] = []
    for index, item in enumerate(raw_fields):
        if not isinstance(item, dict):
            raise OnboardingValidationError(f"daily_log_fields[{index}] must be an object.")
        field_id = _require_string_field(item, "field_id", f"daily_log_fields[{index}]")
        label = _require_string_field(item, "label", f"daily_log_fields[{index}]")
        field_type = item.get("type")
        if not isinstance(field_type, str) or field_type not in _VALID_FIELD_TYPES:
            raise OnboardingValidationError(f"daily_log_fields[{index}].type is invalid.")
        raw_options = item.get("options", [])
        options: list[str] = []
        if isinstance(raw_options, list):
            options = [str(option) for option in raw_options]
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            reason = ""
        fields.append(
            DailyLogSchemaField(
                field_id=field_id,
                label=label,
                type=cast(DailyLogFieldType, field_type),
                options=options,
                display_order=index,
                reason=reason,
            )
        )
    return fields


def _build_profile_medication_jobs(profile: ProfileInput) -> list[ScheduledJob]:
    """Build medication reminder jobs from the user profile."""
    jobs: list[ScheduledJob] = []
    for medication in profile.medications:
        safe_name = medication.name.lower().replace(" ", "_")
        safe_time = medication.time.replace(":", "")
        jobs.append(
            ScheduledJob(
                job_id=f"med_{safe_name}_{safe_time}",
                type="medication",
                schedule_type="daily_time",
                time=medication.time,
                interval_minutes=None,
                days="daily",
                message=f"Time for your {medication.name} ({medication.dose})",
                tts=True,
                active=True,
                context="Daily medication from your profile",
            )
        )
    return jobs


def _parse_scheduled_jobs(raw_jobs: object) -> list[ScheduledJob]:
    """Parse scheduled_jobs from Call 2 and infer schedule_type."""
    if not isinstance(raw_jobs, list):
        raise OnboardingValidationError("scheduled_jobs must be an array.")
    if len(raw_jobs) == 0:
        return []

    jobs: list[ScheduledJob] = []
    for index, item in enumerate(raw_jobs):
        if not isinstance(item, dict):
            raise OnboardingValidationError(f"scheduled_jobs[{index}] must be an object.")
        job_id = _require_string_field(item, "job_id", f"scheduled_jobs[{index}]")
        job_type = item.get("type")
        if not isinstance(job_type, str) or job_type not in _VALID_JOB_TYPES:
            raise OnboardingValidationError(f"scheduled_jobs[{index}].type is invalid.")
        message = _require_string_field(item, "message", f"scheduled_jobs[{index}]")
        context = item.get("context", "")
        if not isinstance(context, str):
            context = ""
        tts_value = item.get("tts", False)
        tts = bool(tts_value)
        days = item.get("days", "daily")
        if not isinstance(days, str):
            days = "daily"
        schedule_type = item.get("schedule_type")
        time_value = item.get("time")
        interval_minutes = item.get("interval_minutes")

        parsed_time: str | None = None
        parsed_interval: int | None = None
        parsed_schedule_type = "daily_time"

        if isinstance(schedule_type, str) and schedule_type in {
            "daily_time",
            "interval_minutes",
            "weekly",
        }:
            parsed_schedule_type = schedule_type

        if parsed_schedule_type == "interval_minutes":
            if not isinstance(interval_minutes, int) or interval_minutes < 1:
                raise OnboardingValidationError(
                    f"scheduled_jobs[{index}].interval_minutes must be a positive integer."
                )
            parsed_interval = interval_minutes
        else:
            if not isinstance(time_value, str) or not _TIME_PATTERN.match(time_value):
                raise OnboardingValidationError(
                    f"scheduled_jobs[{index}].time must be HH:MM."
                )
            parsed_time = time_value
            parsed_schedule_type = "daily_time"

        jobs.append(
            ScheduledJob(
                job_id=job_id,
                type=cast(ScheduledJobType, job_type),
                schedule_type=cast(ScheduleType, parsed_schedule_type),
                time=parsed_time,
                interval_minutes=parsed_interval,
                days=days,
                message=message,
                tts=tts,
                active=True,
                context=context,
            )
        )
    return jobs


def _parse_meal_plan_framework(raw_value: object) -> MealPlanFramework:
    """Parse meal_plan_framework from Call 2."""
    if not isinstance(raw_value, dict):
        raise OnboardingValidationError("meal_plan_framework must be an object.")
    prioritise = raw_value.get("nutrients_to_prioritise", [])
    moderate = raw_value.get("nutrients_to_moderate", [])
    meal_frequency = raw_value.get("meal_frequency", 3)
    notes = raw_value.get("notes", "")
    if not isinstance(prioritise, list):
        prioritise = []
    if not isinstance(moderate, list):
        moderate = []
    if not isinstance(meal_frequency, int):
        meal_frequency = 3
    if not isinstance(notes, str):
        notes = ""
    return MealPlanFramework(
        nutrients_to_prioritise=[str(item) for item in prioritise],
        nutrients_to_moderate=[str(item) for item in moderate],
        meal_frequency=meal_frequency,
        notes=notes,
    )


def _parse_exercise_plan(raw_value: object) -> ExercisePlan:
    """Parse exercise_plan from Call 2."""
    if not isinstance(raw_value, dict):
        raise OnboardingValidationError("exercise_plan must be an object.")
    frequency = raw_value.get("frequency", "daily")
    intensity = raw_value.get("intensity", "light")
    duration = raw_value.get("session_duration_minutes", 20)
    types = raw_value.get("types", [])
    avoid = raw_value.get("avoid", [])
    notes = raw_value.get("notes", "")
    if not isinstance(frequency, str):
        frequency = "daily"
    if not isinstance(intensity, str):
        intensity = "light"
    if not isinstance(duration, int):
        duration = 20
    if not isinstance(types, list):
        types = []
    if not isinstance(avoid, list):
        avoid = []
    if not isinstance(notes, str):
        notes = ""
    return ExercisePlan(
        frequency=frequency,
        intensity=intensity,
        session_duration_minutes=duration,
        types=[str(item) for item in types],
        avoid=[str(item) for item in avoid],
        notes=notes,
    )


def _parse_weekly_check_structure(raw_value: object) -> WeeklyCheckStructure:
    """Parse weekly_check_structure from Call 2."""
    if not isinstance(raw_value, dict):
        raise OnboardingValidationError("weekly_check_structure must be an object.")
    return WeeklyCheckStructure(
        report_day=_require_string_field(raw_value, "report_day", "weekly_check_structure"),
        report_time=_require_string_field(raw_value, "report_time", "weekly_check_structure"),
        replan_day=_require_string_field(raw_value, "replan_day", "weekly_check_structure"),
        replan_time=_require_string_field(raw_value, "replan_time", "weekly_check_structure"),
    )


def _parse_presence_check(raw_value: object) -> PresenceConfig:
    """Parse presence_check from Call 2."""
    if not isinstance(raw_value, dict):
        raise OnboardingValidationError("presence_check must be an object.")
    enabled = raw_value.get("enabled", True)
    max_minutes = raw_value.get("max_continuous_minutes", 30)
    break_message = raw_value.get("break_message", "")
    break_duration = raw_value.get("break_duration_minutes", 5)
    if not isinstance(enabled, bool):
        enabled = True
    if not isinstance(max_minutes, int):
        max_minutes = 30
    if not isinstance(break_message, str) or not break_message.strip():
        break_message = "You have been at your desk for a while. Stand up, stretch, and drink water."
    if not isinstance(break_duration, int):
        break_duration = 5
    return PresenceConfig(
        enabled=enabled,
        max_continuous_minutes=max_minutes,
        break_message=break_message.strip(),
        break_duration_minutes=break_duration,
        check_interval_seconds=300,
    )


def _parse_coach_quick_questions(raw_value: object) -> list[str]:
    """Parse coach quick-question chips from Call 2."""
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise OnboardingValidationError("coach_quick_questions must be an array.")
    questions = [str(item).strip() for item in raw_value if str(item).strip()]
    return questions[:6]


def _contains_placeholder_text(value: str) -> bool:
    """Return True when text still looks like an unfilled schema example."""
    lowered = value.strip().lower()
    if not lowered:
        return False
    return any(phrase in lowered for phrase in _PLACEHOLDER_PHRASES)


def _reject_placeholder_text(value: str, field_label: str) -> None:
    """Raise when LLM output still contains template placeholder wording."""
    if _contains_placeholder_text(value):
        raise OnboardingValidationError(
            f"{field_label} still contains placeholder text — regenerate with user-specific content."
        )


def finalize_plan_for_profile(profile: ProfileInput, plan: OnboardingPlan) -> OnboardingPlan:
    """Strip invalid jobs, reject placeholders, and personalize spoken messages."""
    for field in plan.daily_log_fields:
        _reject_placeholder_text(field.reason, f"daily_log_fields.{field.field_id}.reason")

    _reject_placeholder_text(plan.system_prompt_additions, "system_prompt_additions")
    _reject_placeholder_text(plan.meal_plan_framework.notes, "meal_plan_framework.notes")
    _reject_placeholder_text(plan.exercise_plan.notes, "exercise_plan.notes")

    for job in plan.scheduled_jobs:
        _reject_placeholder_text(job.message, f"scheduled_jobs.{job.job_id}.message")
        if job.context:
            _reject_placeholder_text(job.context, f"scheduled_jobs.{job.job_id}.context")

    plan.scheduled_jobs = _build_profile_medication_jobs(profile)

    if plan.hydration_goal_liters < 0.5:
        plan.hydration_goal_liters = 2.5

    plan.presence_check.break_message = personalize_message(
        profile.name,
        plan.presence_check.break_message,
    )

    for job in plan.scheduled_jobs:
        if job.tts:
            job.message = personalize_message(profile.name, job.message)

    return plan


def validate_plan_response(payload: dict[str, object]) -> OnboardingPlan:
    """Validate Call 2 JSON and return a parsed onboarding plan."""
    system_prompt_additions = payload.get("system_prompt_additions", "")
    if not isinstance(system_prompt_additions, str):
        system_prompt_additions = ""

    hydration_raw = payload.get("hydration_goal_liters", 2.5)
    hydration_goal = 2.5
    if isinstance(hydration_raw, (int, float)):
        hydration_goal = max(0.5, float(hydration_raw))

    return OnboardingPlan(
        daily_log_fields=_parse_daily_log_fields(payload.get("daily_log_fields")),
        scheduled_jobs=_parse_scheduled_jobs(payload.get("scheduled_jobs")),
        meal_plan_framework=_parse_meal_plan_framework(payload.get("meal_plan_framework")),
        exercise_plan=_parse_exercise_plan(payload.get("exercise_plan")),
        weekly_check_structure=_parse_weekly_check_structure(payload.get("weekly_check_structure")),
        presence_check=_parse_presence_check(payload.get("presence_check")),
        system_prompt_additions=system_prompt_additions.strip(),
        hydration_goal_liters=hydration_goal,
        coach_quick_questions=_parse_coach_quick_questions(payload.get("coach_quick_questions")),
    )


def plan_to_commit_data(profile: ProfileInput, plan: OnboardingPlan) -> OnboardingCommitData:
    """Bundle profile and validated plan for the database commit."""
    return OnboardingCommitData(profile=profile, plan=plan)
