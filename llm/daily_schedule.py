"""Daily schedule generation — LLM prompt, tools, validation, and fallbacks."""

import json
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import cast

from db import queries
from vital_types.daily_plan import DailyPlan, DailyScheduleJob
from vital_types.db import ProfileInput, ScheduledJobType

from core.personalization import personalize_message
from core.weather import get_cached_weather
from llm.client import get_llm_client

logger = logging.getLogger(__name__)

_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_VALID_JOB_TYPES = {"hydration", "exercise", "meal", "check_in", "break"}
_MIN_HYDRATION_JOBS = 4
_MAX_HYDRATION_JOBS = 12
_MIN_MEAL_JOBS = 3
_MIN_SLOT_GAP_MINUTES = 30
_EXERCISE_PREP_MINUTES = 10

_TIME_JSON_PATTERN = "^([01][0-9]|2[0-3]):[0-5][0-9]$"

DAILY_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 10, "maxLength": 400},
        "hydration_goal_liters": {"type": "number", "minimum": 0.5, "maximum": 8},
        "jobs": {
            "type": "array",
            "minItems": 4,
            "maxItems": 14,
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "type": {
                        "type": "string",
                        "enum": ["hydration", "meal", "exercise", "check_in", "break"],
                    },
                    "time": {"type": "string", "pattern": _TIME_JSON_PATTERN},
                    "message": {"type": "string", "minLength": 5, "maxLength": 200},
                    "tts": {"type": "boolean"},
                    "context": {"type": "string", "maxLength": 120},
                    "volume_ml": {"type": ["integer", "null"], "minimum": 50, "maximum": 1500},
                    "exercise_type": {"type": ["string", "null"]},
                    "duration_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 120},
                },
                "required": ["job_id", "type", "time", "message", "tts", "context"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "hydration_goal_liters", "jobs"],
    "additionalProperties": False,
}

DAILY_SCHEDULE_SYSTEM = """
You are Vitál's daily planner. You design ONE user's complete wellness timetable for the target
date: hydration reminders, three meals, and one exercise session. You are NOT the conversational
coach — you output a single JSON plan the app will schedule and speak aloud.

WHAT YOU RECEIVE:
- Profile: name, age, city, goal, conditions, triggers, wake/sleep, desk_worker, exercise_level,
  dietary_notes, local_foods.
- Onboarding frameworks already saved in the database:
  • hydration_goal_liters — total water target for the day.
  • exercise_plan — allowed types, frequency, intensity, session_duration_minutes, avoid list, notes.
  • meal_plan_framework — nutrients_to_prioritise, nutrients_to_moderate, notes.
- Medication list with fixed times (scheduled separately — never duplicate as jobs).
- Yesterday's logs and today's logs so far; optional weather.

ALL the data you need is already in the user message — do not ask for more information.
Return ONE JSON object — no markdown, no commentary.

HYDRATION:
- Split hydration_goal_liters evenly across spaced jobs between wake_time and sleep_time. 
- Hydration goal should be best based on user's profile, conditions and goals.
- Each job: type hydration, volume_ml (integer millilitres), message stating the amount to drink.
- Sum of volume_ml should approximate the daily goal (±10%).

EXERCISE (exactly ONE job):
- Read exercise_plan.types — pick one allowed type; respect avoid list and notes.
- Use session_duration_minutes for duration_minutes; match intensity to exercise_level and conditions.
- Avoid scheduling within 30 minutes of any medication time.
- Pick a realistic slot: not at wake, not near sleep, not during typical work deep-focus if desk_worker.
  Late afternoon (e.g. 16:00–18:00) often suits desk workers with light/moderate plans.
- Include exercise_type and duration_minutes on the job.
- Do NOT add a separate prep/heads-up job — the app injects a 10-minute spoken reminder automatically.

MEALS (exactly THREE jobs: breakfast, lunch, dinner):
- type meal; job_id should include breakfast, lunch, or dinner.
- Use local_foods and meal_plan_framework.nutrients_to_prioritise in each suggestion.
- Respect nutrients_to_moderate and dietary_notes (allergies, restrictions).
- Read "Meals already eaten earlier this week" in the user message — vary suggestions
  for variety and avoid repeating the same dish on consecutive days when possible.
- Message format: what to eat + brief why (folate, iron, hydration-friendly, steady energy, etc.).
- Space meals across the waking day; breakfast after wake, dinner at least 90 minutes before sleep.

MEDICATIONS:
- Never emit medication jobs — they are loaded from the profile automatically.

TIMING:
- All times HH:MM between wake_time and sleep_time (sleep may be next calendar day).
- If plan_date is today, every time must be >= current time in the user message.
- Never use 00:00 unless wake_time is 00:00.
- Keep at least 30 minutes between any two jobs when possible.

MESSAGES & TTS:
- Short, natural, spoken-aloud. Do NOT prefix the user's name (the app adds it).
- Example messages:
  - "Time to take a water break. Drink 350ml of water now"
  - "It's time to eat. Dinner: beans and plantain for folate and steady energy"
  - "Let's get some exercise. 20 minutes of gentle walking"
- Set tts true for hydration, exercise, and meal jobs unless context clearly says otherwise.
- context field: one sentence on why this slot helps this user.

Never diagnose or prescribe. Return ONLY valid JSON matching the schema in the user message.
"""


class DailyScheduleValidationError(Exception):
    """Raised when daily schedule LLM output fails validation."""


def _time_to_minutes(time_value: str) -> int:
    """Convert HH:MM to minutes from midnight."""
    hour_text, minute_text = time_value.split(":", 1)
    return int(hour_text) * 60 + int(minute_text)


def _minutes_to_time(total_minutes: int) -> str:
    """Convert minutes from midnight to HH:MM."""
    clamped = max(0, min(total_minutes, 23 * 60 + 59))
    hours = clamped // 60
    minutes = clamped % 60
    return f"{hours:02d}:{minutes:02d}"


def _effective_sleep_minutes(wake_minutes: int, sleep_minutes: int) -> int:
    """Treat sleep time as next-day when it is earlier than wake on the clock."""
    if sleep_minutes <= wake_minutes:
        return sleep_minutes + 24 * 60
    return sleep_minutes


def is_past_wake_time(profile: ProfileInput, now: datetime | None = None) -> bool:
    """Return True when the current clock time is at or after the user's wake time."""
    current = now or datetime.now()
    wake_minutes = _time_to_minutes(profile.wake_time)
    current_minutes = current.hour * 60 + current.minute
    return current_minutes >= wake_minutes


def should_generate_daily_plan(plan_date: date, now: datetime | None = None) -> bool:
    """Return True when today's plan should be generated now."""
    if queries.has_daily_plan(plan_date):
        return False
    profile = queries.get_profile()
    if profile is None:
        return False
    if not queries.check_onboarding_status():
        return False
    return is_past_wake_time(profile, now)


def build_daily_schedule_context(profile: ProfileInput, plan_date: date) -> str:
    """Assemble context for the daily schedule LLM prompt."""
    yesterday = plan_date - timedelta(days=1)
    yesterday_logs = queries.get_daily_logs_for_date(yesterday)
    today_logs = queries.get_daily_logs_for_date(plan_date)
    exercise_plan = queries.get_exercise_plan() or {}
    meal_framework = queries.get_meal_plan_framework() or {}
    hydration_goal = queries.get_hydration_goal_liters()
    weather = get_cached_weather()
    now = datetime.now()
    med_lines = [
        f"- {med.name} {med.dose} at {med.time}" for med in profile.medications
    ]

    log_lines = [f"- {entry.field_id}: {entry.value}" for entry in yesterday_logs]
    today_lines = [f"- {entry.field_id}: {entry.value}" for entry in today_logs]

    week_start = queries.monday_of_week(plan_date)
    prior_week_meals: list[str] = []
    if plan_date > week_start:
        prior_entries = queries.get_food_logs_between(week_start, plan_date - timedelta(days=1))
        for entry in prior_entries:
            prior_week_meals.append(
                f"- {entry.date.isoformat()} {entry.meal_type}: {entry.food_description}"
            )
    prior_meals_block = (
        "\n".join(prior_week_meals) if prior_week_meals else "  (none earlier this week)"
    )

    return (
        f"Today: {plan_date.isoformat()} ({now.strftime('%A')})\n"
        f"Current time: {now.strftime('%H:%M')}\n"
        f"Wake: {profile.wake_time} | Sleep: {profile.sleep_time}\n"
        f"Hydration goal: {hydration_goal} litres\n"
        f"Weather: {weather.summary if weather else 'unavailable'}\n"
        f"Goal: {profile.goal}\n"
        f"Conditions: {', '.join(profile.conditions) or 'none'}\n"
        f"Triggers: {', '.join(profile.triggers) or 'none'}\n"
        f"Dietary notes: {profile.dietary_notes or 'none'}\n"
        f"Local foods: {profile.local_foods or 'none'}\n"
        f"Desk worker: {profile.desk_worker} | Exercise level: {profile.exercise_level}\n"
        f"Medications (already scheduled separately):\n"
        + ("\n".join(med_lines) if med_lines else "  (none)")
        + f"\nExercise framework: {json.dumps(exercise_plan)}\n"
        f"Nutrition framework: {json.dumps(meal_framework)}\n"
        f"Yesterday's logs:\n" + ("\n".join(log_lines) if log_lines else "  (none)") + "\n"
        f"Today's logs so far:\n" + ("\n".join(today_lines) if today_lines else "  (none)") + "\n"
        f"Meals already eaten earlier this week (vary suggestions — do not repeat blindly):\n"
        f"{prior_meals_block}"
    )


def _is_remainder_of_day_plan(profile: ProfileInput, plan_date: date) -> bool:
    """Return True when planning only the rest of today (past wake time)."""
    if plan_date != date.today():
        return False
    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    wake_minutes = _time_to_minutes(profile.wake_time)
    return now_minutes > wake_minutes


def _required_meal_count(profile: ProfileInput, plan_date: date) -> int:
    """Minimum meals required based on how much of the day remains."""
    if not _is_remainder_of_day_plan(profile, plan_date):
        return _MIN_MEAL_JOBS
    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    wake_minutes = _time_to_minutes(profile.wake_time)
    if now_minutes >= wake_minutes + 14 * 60:
        return 1
    if now_minutes >= wake_minutes + 10 * 60:
        return 2
    return _MIN_MEAL_JOBS


def _required_hydration_count(profile: ProfileInput, plan_date: date) -> int:
    """Minimum hydration reminders based on remaining waking hours."""
    if not _is_remainder_of_day_plan(profile, plan_date):
        return _MIN_HYDRATION_JOBS
    wake_minutes = _time_to_minutes(profile.wake_time)
    sleep_minutes = _effective_sleep_minutes(
        wake_minutes,
        _time_to_minutes(profile.sleep_time),
    )
    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    span_minutes = max(0, sleep_minutes - max(now_minutes, wake_minutes))
    if span_minutes < 180:
        return 2
    if span_minutes < 300:
        return 3
    return 4


def _remainder_day_prompt_block(profile: ProfileInput, now: datetime) -> str:
    """Extra instructions when generating a partial-day schedule."""
    min_meals = _required_meal_count(profile, date.today())
    min_water = _required_hydration_count(profile, date.today())
    meal_note = (
        "Include dinner (and a light evening snack if helpful)."
        if min_meals == 1
        else f"Include {min_meals} meal job(s) for the time left today."
    )
    return f"""
*** REMAINDER-OF-DAY PLANNING (CRITICAL) ***
Current time is {now.strftime('%H:%M')} on {now.strftime('%A')}. You are scheduling ONLY from now until {profile.sleep_time}.
Every job time MUST be >= {now.strftime('%H:%M')}. Do NOT use morning or afternoon times that have already passed.
{meal_note}
Use {min_water}–5 hydration jobs for the hours remaining (not a full-day count).
"""


def _reschedule_past_jobs_for_today(
    raw_jobs: list[object],
    profile: ProfileInput,
    plan_date: date,
) -> list[object]:
    """Shift past job times forward when the model returns a full-day template."""
    if plan_date != date.today():
        return raw_jobs

    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    wake_minutes = _time_to_minutes(profile.wake_time)
    sleep_minutes = _effective_sleep_minutes(
        wake_minutes,
        _time_to_minutes(profile.sleep_time),
    )
    start_minutes = max(now_minutes + 10, wake_minutes)
    end_minutes = sleep_minutes - 30
    if end_minutes <= start_minutes:
        return raw_jobs

    dict_jobs = [item for item in raw_jobs if isinstance(item, dict)]
    if not dict_jobs:
        return raw_jobs

    has_past = False
    for item in dict_jobs:
        time_value = item.get("time")
        if isinstance(time_value, str) and _time_to_minutes(time_value) < now_minutes:
            has_past = True
            break
    if not has_past:
        return raw_jobs

    sorted_jobs = sorted(
        dict_jobs,
        key=lambda item: _time_to_minutes(str(item.get("time", "00:00"))),
    )
    gap = max(_MIN_SLOT_GAP_MINUTES, (end_minutes - start_minutes) // max(len(sorted_jobs), 1))
    logger.info(
        "[daily_schedule] LLM returned past times — rescheduling %s jobs from %s to %s.",
        len(sorted_jobs),
        _minutes_to_time(start_minutes),
        _minutes_to_time(end_minutes),
    )

    rescheduled: list[object] = []
    for index, item in enumerate(sorted_jobs):
        updated = dict(item)
        slot = min(start_minutes + gap * index, end_minutes)
        updated["time"] = _minutes_to_time(slot)
        rescheduled.append(updated)
    return rescheduled


def build_daily_schedule_user_prompt(profile: ProfileInput, plan_date: date) -> str:
    """Build the user prompt for daily schedule generation."""
    context = build_daily_schedule_context(profile, plan_date)
    hydration_goal = queries.get_hydration_goal_liters()
    now = datetime.now()
    remainder_block = ""
    example_time = "08:30"
    if _is_remainder_of_day_plan(profile, plan_date):
        remainder_block = _remainder_day_prompt_block(profile, now)
        example_time = _minutes_to_time(
            max(
                now.hour * 60 + now.minute + 15,
                _time_to_minutes(profile.wake_time),
            )
        )
    min_meals = _required_meal_count(profile, plan_date)
    min_water = _required_hydration_count(profile, plan_date)
    return f"""
USER PROFILE
Name: {profile.name} | Age: {profile.age} | City: {profile.city} | Profession: {profile.profession}

{context}
{remainder_block}
Return JSON with this exact shape:
{{
  "summary": "<2-3 sentences: today's focus for {profile.name}>",
  "hydration_goal_liters": {hydration_goal},
  "jobs": [
    {{
      "job_id": "water_{example_time.replace(':', '')}",
      "type": "hydration",
      "time": "{example_time}",
      "message": "Drink 350ml of water now",
      "volume_ml": 350,
      "tts": true,
      "context": "why this helps this user"
    }},
    {{
      "job_id": "dinner_2000",
      "type": "meal",
      "time": "20:00",
      "message": "Dinner: beans and plantain for folate and steady energy",
      "tts": true,
      "context": "nutrition rationale"
    }},
    {{
      "job_id": "exercise_1900",
      "type": "exercise",
      "time": "19:00",
      "message": "20 minutes of gentle walking",
      "exercise_type": "walking",
      "duration_minutes": 20,
      "tts": true,
      "context": "safety note for this user"
    }}
  ]
}}

Requirements:
- {min_water}–5 hydration jobs for the relevant window (compact output)
- At least {min_meals} meal job(s) with specific food suggestions
- Exactly 1 exercise job using exercise_plan types and duration
- All times between {profile.wake_time} and {profile.sleep_time}
- Every time MUST be >= {now.strftime('%H:%M')} when planning for today
- job_id must be unique snake_case
- Keep each context field under 60 characters so the full JSON fits in one response
"""


def validate_daily_schedule_response(
    payload: dict[str, object],
    profile: ProfileInput,
    plan_date: date,
) -> DailyPlan:
    """Validate LLM daily schedule JSON and return a parsed plan."""
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise DailyScheduleValidationError("summary must be a non-empty string.")

    hydration_raw = payload.get("hydration_goal_liters", queries.get_hydration_goal_liters())
    if isinstance(hydration_raw, (int, float)):
        hydration_goal = float(hydration_raw)
    else:
        hydration_goal = queries.get_hydration_goal_liters()

    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list) or len(raw_jobs) == 0:
        raise DailyScheduleValidationError("jobs must be a non-empty array.")

    raw_jobs = _reschedule_past_jobs_for_today(raw_jobs, profile, plan_date)
    min_meals = _required_meal_count(profile, plan_date)
    min_hydration = _required_hydration_count(profile, plan_date)

    wake_minutes = _time_to_minutes(profile.wake_time)
    sleep_minutes = _effective_sleep_minutes(wake_minutes, _time_to_minutes(profile.sleep_time))
    now_minutes = datetime.now().hour * 60 + datetime.now().minute

    jobs: list[DailyScheduleJob] = []
    hydration_count = 0
    meal_count = 0
    exercise_count = 0

    for index, item in enumerate(raw_jobs):
        if not isinstance(item, dict):
            logger.warning("[daily_schedule] Skipping jobs[%s]: not an object.", index)
            continue
        job_id = item.get("job_id")
        job_type = item.get("type")
        time_value = item.get("time")
        message = item.get("message")
        if not isinstance(job_id, str) or not job_id.strip():
            logger.warning("[daily_schedule] Skipping jobs[%s]: missing job_id.", index)
            continue
        if not isinstance(job_type, str) or job_type not in _VALID_JOB_TYPES:
            logger.warning(
                "[daily_schedule] Skipping jobs[%s]: invalid type %r.", index, job_type
            )
            continue
        if not isinstance(time_value, str) or not _TIME_PATTERN.match(time_value):
            logger.warning(
                "[daily_schedule] Skipping jobs[%s]: invalid time %r.", index, time_value
            )
            continue
        parsed_time = time_value
        if not isinstance(message, str) or not message.strip():
            logger.warning("[daily_schedule] Skipping jobs[%s]: missing message.", index)
            continue

        # Repair out-of-window times by clamping instead of rejecting the plan.
        slot_minutes = _time_to_minutes(parsed_time)
        lower_bound = wake_minutes
        if plan_date == date.today():
            lower_bound = max(wake_minutes, now_minutes + 5)
        upper_bound = sleep_minutes - 15 if sleep_minutes < 24 * 60 else 23 * 60 + 45
        if slot_minutes < lower_bound or slot_minutes > upper_bound:
            clamped_minutes = min(max(slot_minutes, lower_bound), upper_bound)
            logger.info(
                "[daily_schedule] Clamped jobs[%s] time %s -> %s.",
                index,
                parsed_time,
                _minutes_to_time(clamped_minutes),
            )
            parsed_time = _minutes_to_time(clamped_minutes)

        context = item.get("context", "")
        if not isinstance(context, str):
            context = ""
        tts_value = item.get("tts", True)
        tts = bool(tts_value)

        volume_ml: int | None = None
        exercise_type: str | None = None
        duration_minutes: int | None = None

        if job_type == "hydration":
            hydration_count += 1
            raw_volume = item.get("volume_ml")
            if isinstance(raw_volume, int) and raw_volume > 0:
                volume_ml = raw_volume
        if job_type == "meal":
            meal_count += 1
        if job_type == "exercise":
            exercise_count += 1
            raw_exercise_type = item.get("exercise_type")
            raw_duration = item.get("duration_minutes")
            if isinstance(raw_exercise_type, str) and raw_exercise_type.strip():
                exercise_type = raw_exercise_type.strip()
            if isinstance(raw_duration, int) and raw_duration > 0:
                duration_minutes = raw_duration

        jobs.append(
            DailyScheduleJob(
                job_id=job_id.strip(),
                type=cast(ScheduledJobType, job_type),
                time=parsed_time,
                message=message.strip(),
                tts=tts,
                context=context.strip(),
                volume_ml=volume_ml,
                exercise_type=exercise_type,
                duration_minutes=duration_minutes,
            )
        )

    if not jobs:
        raise DailyScheduleValidationError("No usable jobs after parsing the LLM response.")

    jobs = _repair_plan_jobs(
        jobs,
        profile,
        plan_date,
        hydration_goal,
        min_hydration=min_hydration,
        min_meals=min_meals,
        hydration_count=hydration_count,
        meal_count=meal_count,
        exercise_count=exercise_count,
    )

    plan = DailyPlan(
        plan_date=plan_date,
        summary=summary.strip(),
        hydration_goal_liters=hydration_goal,
        generated_at=datetime.now(),
        jobs=jobs,
    )
    return finalize_daily_plan(plan, profile)


def _open_slot_minutes(
    jobs: list[DailyScheduleJob],
    start_minutes: int,
    end_minutes: int,
) -> int:
    """Find a free minute slot at least 20 minutes away from existing jobs."""
    taken = sorted(_time_to_minutes(job.time) for job in jobs)
    candidate = start_minutes
    while candidate <= end_minutes:
        if all(abs(candidate - slot) >= 20 for slot in taken):
            return candidate
        candidate += 20
    return min(max(start_minutes, end_minutes), 23 * 60 + 45)


def _repair_plan_jobs(
    jobs: list[DailyScheduleJob],
    profile: ProfileInput,
    plan_date: date,
    hydration_goal: float,
    min_hydration: int,
    min_meals: int,
    hydration_count: int,
    meal_count: int,
    exercise_count: int,
) -> list[DailyScheduleJob]:
    """Pad missing meals/water/exercise and trim extras instead of rejecting."""
    wake_minutes = _time_to_minutes(profile.wake_time)
    sleep_minutes = _effective_sleep_minutes(wake_minutes, _time_to_minutes(profile.sleep_time))
    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    start_minutes = wake_minutes
    if plan_date == date.today():
        start_minutes = max(wake_minutes, now_minutes + 10)
    end_minutes = (sleep_minutes if sleep_minutes < 24 * 60 else 23 * 60 + 45) - 30

    repaired = list(jobs)

    # Keep only the earliest exercise job; the plan needs exactly one.
    if exercise_count > 1:
        exercise_jobs = sorted(
            (job for job in repaired if job.type == "exercise"),
            key=lambda job: _time_to_minutes(job.time),
        )
        for extra in exercise_jobs[1:]:
            repaired.remove(extra)
            logger.info("[daily_schedule] Dropped extra exercise job %s.", extra.job_id)

    if exercise_count == 0:
        exercise_plan = queries.get_exercise_plan() or {}
        types_value = exercise_plan.get("types", ["walking"])
        exercise_type = (
            types_value[0] if isinstance(types_value, list) and types_value else "walking"
        )
        duration = exercise_plan.get("session_duration_minutes", 20)
        if not isinstance(duration, int):
            duration = 20
        slot = _open_slot_minutes(repaired, start_minutes + 60, end_minutes - 20)
        slot_time = _minutes_to_time(slot)
        repaired.append(
            DailyScheduleJob(
                job_id=f"exercise_{slot_time.replace(':', '')}",
                type="exercise",
                time=slot_time,
                message=f"Time for {duration} minutes of {exercise_type}.",
                tts=True,
                context="Added by Vitál to meet your exercise plan.",
                exercise_type=str(exercise_type),
                duration_minutes=duration,
            )
        )
        logger.info("[daily_schedule] Padded missing exercise job at %s.", slot_time)

    if meal_count < min_meals:
        meal_framework = queries.get_meal_plan_framework() or {}
        local_foods = profile.local_foods or "local staples"
        prioritise = meal_framework.get("nutrients_to_prioritise", ["balanced nutrients"])
        nutrient = (
            prioritise[0]
            if isinstance(prioritise, list) and prioritise
            else "balanced nutrients"
        )
        existing_meals = {job.job_id.split("_")[0] for job in repaired if job.type == "meal"}
        meal_candidates = [
            ("breakfast", wake_minutes + 45),
            ("lunch", wake_minutes + 5 * 60 + 30),
            ("dinner", end_minutes - 60),
        ]
        for meal_name, preferred in meal_candidates:
            if meal_count >= min_meals:
                break
            if meal_name in existing_meals:
                continue
            if preferred < start_minutes or preferred > end_minutes:
                continue
            slot = _open_slot_minutes(repaired, preferred, end_minutes)
            slot_time = _minutes_to_time(slot)
            repaired.append(
                DailyScheduleJob(
                    job_id=f"{meal_name}_{slot_time.replace(':', '')}",
                    type="meal",
                    time=slot_time,
                    message=f"{meal_name.title()}: {local_foods} — focus on {nutrient}",
                    tts=True,
                    context="Added by Vitál from your meal framework.",
                )
            )
            meal_count += 1
            logger.info("[daily_schedule] Padded missing %s job at %s.", meal_name, slot_time)

    if hydration_count < min_hydration:
        missing = min_hydration - hydration_count
        volume_ml = max(150, int((hydration_goal * 1000) / max(min_hydration, 1)))
        for _index in range(missing):
            slot = _open_slot_minutes(repaired, start_minutes + 30, end_minutes)
            slot_time = _minutes_to_time(slot)
            repaired.append(
                DailyScheduleJob(
                    job_id=f"water_{slot_time.replace(':', '')}",
                    type="hydration",
                    time=slot_time,
                    message=f"Drink about {volume_ml}ml of water.",
                    tts=True,
                    context="Added by Vitál to meet your hydration goal.",
                    volume_ml=volume_ml,
                )
            )
            logger.info("[daily_schedule] Padded hydration job at %s.", slot_time)

    return repaired


def finalize_daily_plan(plan: DailyPlan, profile: ProfileInput) -> DailyPlan:
    """Personalize messages, add exercise prep, and sort by time.

    TTS preferences are intentionally NOT baked in here — they are applied
    at fire time in db.queries so Settings changes take effect immediately.
    """
    finalized_jobs: list[DailyScheduleJob] = []
    for job in plan.jobs:
        finalized_jobs.append(
            replace(job, message=personalize_message(profile.name, job.message))
        )

    finalized_jobs = _add_exercise_prep_jobs(finalized_jobs, profile)
    finalized_jobs.sort(key=lambda item: _time_to_minutes(item.time))
    return replace(plan, jobs=finalized_jobs)


def _add_exercise_prep_jobs(
    jobs: list[DailyScheduleJob],
    profile: ProfileInput,
) -> list[DailyScheduleJob]:
    """Insert a 10-minute exercise heads-up before each main exercise job."""
    output: list[DailyScheduleJob] = list(jobs)
    prep_ids = {job.job_id for job in jobs if job.job_id.startswith("exercise_prep_")}

    for job in jobs:
        if job.type != "exercise" or job.job_id.startswith("exercise_prep_"):
            continue
        prep_minutes = _time_to_minutes(job.time) - _EXERCISE_PREP_MINUTES
        if prep_minutes < 0:
            continue
        prep_id = f"exercise_prep_{job.job_id}"
        if prep_id in prep_ids:
            continue
        exercise_label = job.exercise_type or "exercise"
        prep_message = personalize_message(
            profile.name,
            f"In 10 minutes: {exercise_label} for {job.duration_minutes or 20} minutes.",
        )
        output.append(
            DailyScheduleJob(
                job_id=prep_id,
                type="check_in",
                time=_minutes_to_time(prep_minutes),
                message=prep_message,
                tts=True,
                context=f"Heads-up before {exercise_label} at {job.time}.",
                exercise_type=job.exercise_type,
                duration_minutes=job.duration_minutes,
            )
        )
        prep_ids.add(prep_id)

    return output


def copy_daily_plan_from_previous(
    profile: ProfileInput,
    plan_date: date,
    source_plan: DailyPlan,
) -> DailyPlan:
    """Clone a previous day's plan onto a new date."""
    copied_jobs: list[DailyScheduleJob] = []
    for job in source_plan.jobs:
        if job.job_id.startswith("exercise_prep_"):
            continue
        copied_jobs.append(
            DailyScheduleJob(
                job_id=job.job_id,
                type=job.type,
                time=job.time,
                message=job.message,
                tts=job.tts,
                context=job.context,
                volume_ml=job.volume_ml,
                exercise_type=job.exercise_type,
                duration_minutes=job.duration_minutes,
            )
        )

    summary = (
        f"Carried over from {source_plan.plan_date.isoformat()} "
        f"(coach unavailable): {source_plan.summary}"
    )
    plan = DailyPlan(
        plan_date=plan_date,
        summary=summary,
        hydration_goal_liters=queries.get_hydration_goal_liters(),
        generated_at=datetime.now(),
        jobs=copied_jobs,
    )
    return finalize_daily_plan(plan, profile)


def build_template_fallback_daily_plan(profile: ProfileInput, plan_date: date) -> DailyPlan:
    """Build a deterministic plan when LLM fails and no previous day exists."""
    hydration_goal = queries.get_hydration_goal_liters()
    wake_minutes = _time_to_minutes(profile.wake_time)
    sleep_minutes = _effective_sleep_minutes(
        wake_minutes,
        _time_to_minutes(profile.sleep_time),
    )
    now_minutes = datetime.now().hour * 60 + datetime.now().minute
    start_minutes = max(wake_minutes, now_minutes) if plan_date == date.today() else wake_minutes

    span_minutes = max(60, sleep_minutes - start_minutes)
    min_hydration = _required_hydration_count(profile, plan_date)
    slot_count = min(
        _MAX_HYDRATION_JOBS,
        max(min_hydration, int(hydration_goal * 2)),
    )
    if span_minutes < 180:
        slot_count = min(slot_count, 3)
    interval = max(_MIN_SLOT_GAP_MINUTES, span_minutes // (slot_count + 1))
    volume_ml = int((hydration_goal * 1000) / slot_count)

    exercise_plan = queries.get_exercise_plan() or {}
    meal_framework = queries.get_meal_plan_framework() or {}
    exercise_types = exercise_plan.get("types", ["walking"])
    exercise_type = (
        exercise_types[0] if isinstance(exercise_types, list) and exercise_types else "walking"
    )
    duration = exercise_plan.get("session_duration_minutes", 20)
    if not isinstance(duration, int):
        duration = 20

    local_foods = profile.local_foods or "local staples"
    prioritise = meal_framework.get("nutrients_to_prioritise", ["balanced nutrients"])
    nutrient = prioritise[0] if isinstance(prioritise, list) and prioritise else "balanced nutrients"

    jobs: list[DailyScheduleJob] = []
    for index in range(slot_count):
        slot_time = _minutes_to_time(start_minutes + interval * (index + 1))
        if _time_to_minutes(slot_time) >= sleep_minutes - 15:
            break
        jobs.append(
            DailyScheduleJob(
                job_id=f"water_{slot_time.replace(':', '')}",
                type="hydration",
                time=slot_time,
                message=f"Drink about {volume_ml}ml of water ({index + 1} of {slot_count} today).",
                tts=True,
                context="Staying hydrated supports your wellness goals.",
                volume_ml=volume_ml,
            )
        )

    meal_slots: list[tuple[str, int]] = []
    breakfast_at = max(start_minutes, wake_minutes + 30)
    lunch_at = start_minutes + span_minutes // 2
    dinner_at = min(sleep_minutes - 90, start_minutes + max(45, span_minutes // 3))
    if start_minutes < wake_minutes + 10 * 60:
        meal_slots.append(("breakfast", breakfast_at))
    if start_minutes < wake_minutes + 14 * 60:
        meal_slots.append(("lunch", lunch_at))
    meal_slots.append(("dinner", dinner_at))
    for meal_name, meal_minutes in meal_slots:
        if meal_minutes <= start_minutes:
            continue
        if meal_minutes >= sleep_minutes - 15:
            continue
        meal_time = _minutes_to_time(meal_minutes)
        jobs.append(
            DailyScheduleJob(
                job_id=f"{meal_name}_{meal_time.replace(':', '')}",
                type="meal",
                time=meal_time,
                message=(
                    f"{meal_name.title()}: {local_foods} — focus on {nutrient}"
                ),
                tts=True,
                context=str(meal_framework.get("notes", "Eat regularly for steady energy.")),
            )
        )

    exercise_minutes = min(sleep_minutes - 50, start_minutes + int(span_minutes * 0.5))
    exercise_minutes = max(exercise_minutes, start_minutes + 30)
    exercise_minutes = min(exercise_minutes, sleep_minutes - 50)
    exercise_time = _minutes_to_time(exercise_minutes)
    jobs.append(
        DailyScheduleJob(
            job_id=f"exercise_{exercise_time.replace(':', '')}",
            type="exercise",
            time=exercise_time,
            message=f"Time for {duration} minutes of {exercise_type}.",
            tts=True,
            context=str(exercise_plan.get("notes", "Move gently and stop if you feel unwell.")),
            exercise_type=str(exercise_type),
            duration_minutes=duration,
        )
    )

    plan = DailyPlan(
        plan_date=plan_date,
        summary=(
            f"{profile.name}, template plan: {hydration_goal}L water, meals, and "
            f"{duration} min {exercise_type}."
        ),
        hydration_goal_liters=hydration_goal,
        generated_at=datetime.now(),
        jobs=jobs,
    )
    return finalize_daily_plan(plan, profile)


def generate_daily_plan(plan_date: date | None = None) -> DailyPlan:
    """Generate or return the daily plan for a date."""
    target_date = plan_date or date.today()
    existing = queries.get_daily_plan(target_date)
    if existing is not None:
        logger.info("Daily plan already exists for %s — loading.", target_date.isoformat())
        return existing

    profile = queries.get_profile()
    if profile is None:
        raise ValueError("Cannot generate daily plan without a profile.")

    logger.info("Generating daily plan for %s (%s)...", target_date.isoformat(), profile.name)
    client = get_llm_client()
    plan: DailyPlan | None = None

    payload: dict[str, object] | None = None
    try:
        payload = client.generate_daily_schedule_json(
            build_daily_schedule_user_prompt(profile, target_date),
            system_prompt=DAILY_SCHEDULE_SYSTEM,
            json_schema=DAILY_PLAN_JSON_SCHEMA,
        )
        plan = validate_daily_schedule_response(payload, profile, target_date)
        logger.info(
            "[daily_schedule] LLM plan for %s: %s jobs (hydration=%s meals=%s exercise prep included).",
            target_date.isoformat(),
            len(plan.jobs),
            sum(1 for job in plan.jobs if job.type == "hydration"),
            sum(1 for job in plan.jobs if job.type == "meal"),
        )
    except (ValueError, DailyScheduleValidationError) as error:
        logger.warning("[daily_schedule] LLM failed for %s: %s", target_date.isoformat(), error)
        if payload is not None:
            logger.warning(
                "[daily_schedule] Rejected payload (for debugging): %s",
                json.dumps(payload)[:1500],
            )
        previous = queries.find_previous_daily_plan(target_date)
        if previous is not None:
            plan = copy_daily_plan_from_previous(profile, target_date, previous)
            logger.info(
                "[daily_schedule] Copied plan from %s → %s (%s jobs).",
                previous.plan_date.isoformat(),
                target_date.isoformat(),
                len(plan.jobs),
            )
        else:
            plan = build_template_fallback_daily_plan(profile, target_date)
            logger.info(
                "[daily_schedule] No previous plan — using template fallback (%s jobs).",
                len(plan.jobs),
            )

    queries.save_daily_plan(plan)
    return plan
