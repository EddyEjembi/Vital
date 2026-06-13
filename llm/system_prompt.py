"""Build the full LLM context prompt from database state and runtime context."""

import json
from datetime import datetime

from db import queries
from core.weather import fetch_weather, get_cached_weather

ROLE_DEFINITION = (
    "You are Vitál, a warm and knowledgeable personal wellness coach. "
    "You help the user build sustainable daily habits around nutrition, movement, "
    "hydration, medication, and energy. Be concise, encouraging, and practical."
)

SAFETY_GUARDRAIL = (
    "You are not a doctor. Never diagnose conditions. "
    "For any serious symptom, tell the user to seek medical attention immediately."
)

TOOL_USAGE_INSTRUCTION = (
    "Read tools (get_todays_logs, get_todays_schedule, get_medications_today, "
    "get_meal_plan, get_weekly_summary, get_recent_logs): use when the user asks "
    "a question or you need data to answer. get_todays_logs returns check_in_logs, "
    "food_logs (meals actually eaten), and exercise_logs — use food_logs to answer "
    "'have I had lunch?' (NOT the schedule). Write tools (log_medication_taken, "
    "log_food, log_water, log_exercise, write_weekly_report, save_meal_plan): use "
    "ONLY when the user's own message explicitly states they just did something — "
    "e.g. 'I took my vitamin', 'I drank 2 cups', 'I had beans for lunch', "
    "'log my lunch'. "
    "FOOD LOGGING RULE: when the user says they ate something or asks you to log a "
    "meal, you MUST call log_food with meal_type (breakfast/lunch/dinner/snack) and "
    "food_description BEFORE your final reply. Never say you logged a meal unless "
    "log_food returned success:true. If they say 'log it' after describing food "
    "earlier, use that description. "
    "Never call a write tool because a read tool returned data, because something "
    "is already marked taken in the database, or because you are summarising "
    "status. Questions like 'what did I take?' or 'what are my logs?' are "
    "read-only — fetch with read tools, then answer in text. "
    "Do not re-log a dose that is already taken. "
    "After tool results arrive, respond naturally without mentioning tools. "
    "Use only these exact tool names (no variations): get_todays_logs, "
    "get_todays_schedule, get_medications_today, get_meal_plan, get_weekly_summary, "
    "get_recent_logs, log_medication_taken, log_food, log_water, log_exercise, "
    "write_weekly_report, save_meal_plan."
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate for prompt budget checks."""
    return max(1, len(text) // 4)


def _period_of_day(now: datetime) -> str:
    """Return morning, afternoon, or evening for the current hour."""
    if now.hour < 12:
        return "morning"
    if now.hour < 17:
        return "afternoon"
    return "evening"


def _format_profile_section() -> str:
    """Build the user profile section of the system prompt."""
    profile = queries.get_profile()
    if profile is None:
        return "User: not yet onboarded."

    medications_text = ", ".join(
        f"{item.name} {item.dose} at {item.time}" for item in profile.medications
    ) or "none"
    conditions_text = ", ".join(profile.conditions) or "none"
    triggers_text = ", ".join(profile.triggers) or "none"

    return (
        f"User: {profile.name} | Age: {profile.age} | City: {profile.city}\n"
        f"Profession: {profile.profession or 'not specified'}\n"
        f"Goal: {profile.goal}\n"
        f"Conditions: {conditions_text}\n"
        f"Medications: {medications_text}\n"
        f"Triggers: {triggers_text}\n"
        f"Wake: {profile.wake_time} | Sleep: {profile.sleep_time}\n"
        f"Desk worker: {profile.desk_worker} | Exercise level: {profile.exercise_level}\n"
        f"Dietary notes: {profile.dietary_notes}\n"
        f"Local foods: {profile.local_foods}"
    )


def _format_context_section(now: datetime | None = None) -> str:
    """Build the current time, weather, and today's logs summary."""
    current = now or datetime.now()
    weather = get_cached_weather() or fetch_weather()
    today = current.date()
    today_logs = queries.get_daily_logs_for_date(today)
    food_logs = queries.get_food_logs_for_date(today)
    exercise_logs = queries.get_exercise_logs_for_date(today)

    log_lines: list[str] = []
    for entry in today_logs:
        log_lines.append(f"  {entry.field_id}: {entry.value}")
    check_in_summary = "\n".join(log_lines) if log_lines else "  (none)"

    food_lines = [
        f"  {entry.meal_type}: {entry.food_description}"
        for entry in food_logs
    ]
    food_summary = "\n".join(food_lines) if food_lines else "  (none)"

    exercise_lines = [
        f"  {entry.exercise_type} — {entry.duration_minutes} min"
        for entry in exercise_logs
        if entry.completed
    ]
    exercise_summary = "\n".join(exercise_lines) if exercise_lines else "  (none)"

    return (
        f"Current time: {current.strftime('%H:%M')} | "
        f"Day: {current.strftime('%A')} | Date: {current.strftime('%A, %B %d, %Y')}\n"
        f"Period: {_period_of_day(current)}\n"
        f"Weather in {weather.city}: {weather.condition}, {weather.temp_c}°C "
        f"(feels like {weather.feels_like_c}°C, humidity {weather.humidity}%)\n"
        f"Today's check-in logs:\n{check_in_summary}\n"
        f"Today's food logs (meals actually eaten):\n{food_summary}\n"
        f"Today's exercise logs:\n{exercise_summary}"
    )


def build_system_prompt(include_tools_instruction: bool = True) -> str:
    """Assemble the full system prompt in PRD order."""
    sections = [
        ROLE_DEFINITION,
        _format_profile_section(),
        _format_context_section(),
        queries.get_system_prompt_additions(),
        SAFETY_GUARDRAIL,
    ]
    if include_tools_instruction:
        sections.append(TOOL_USAGE_INSTRUCTION)

    prompt = "\n\n".join(section for section in sections if section.strip())
    return prompt


def trim_prompt_to_budget(prompt: str, token_limit: int) -> str:
    """Truncate prompt if it exceeds the approximate token budget."""
    if _estimate_tokens(prompt) <= token_limit:
        return prompt
    char_limit = token_limit * 4
    return prompt[:char_limit] + "\n\n[context truncated to fit token budget]"
