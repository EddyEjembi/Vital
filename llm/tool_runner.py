"""Validate and execute LLM tool calls against the local database."""

import json
import re
from datetime import date, datetime, timezone

from db import queries
from vital_types.db import (
    DailyLogEntry,
    ExerciseLogEntry,
    FoodLogEntry,
    MealPlanEntry,
    MedicationLogEntry,
    WeeklyReport,
)
from vital_types.llm import ToolExecutionResult

from llm.tools import TOOL_NAMES

_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
_WEEK_VALUES = {"current", "previous"}


class ToolValidationError(Exception):
    """Raised when tool arguments fail validation."""


def _parse_date_value(value: str, field_name: str) -> date:
    """Validate and parse a YYYY-MM-DD date string."""
    if not _DATE_PATTERN.match(value):
        raise ToolValidationError(f"{field_name} must be YYYY-MM-DD, got '{value}'.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ToolValidationError(f"{field_name} is not a valid date.") from error


def _parse_time_value(value: str) -> str:
    """Validate an HH:MM time string."""
    if not _TIME_PATTERN.match(value):
        raise ToolValidationError(f"time must be HH:MM, got '{value}'.")
    return value


def _require_string(args: dict[str, object], key: str) -> str:
    """Require a non-empty string argument."""
    raw_value = args.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ToolValidationError(f"{key} must be a non-empty string.")
    return raw_value.strip()


def _require_int(args: dict[str, object], key: str, minimum: int, maximum: int) -> int:
    """Require an integer argument within bounds."""
    raw_value = args.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ToolValidationError(f"{key} must be an integer.")
    if raw_value < minimum or raw_value > maximum:
        raise ToolValidationError(f"{key} must be between {minimum} and {maximum}.")
    return raw_value


def _serialize_daily_log(entry: DailyLogEntry) -> dict[str, object]:
    """Convert a daily log entry to a JSON-safe dict."""
    return {
        "date": entry.date.isoformat(),
        "field_id": entry.field_id,
        "value": entry.value,
        "logged_at": entry.logged_at.isoformat(),
    }


def _serialize_medication(entry: MedicationLogEntry) -> dict[str, object]:
    """Convert a medication log entry to a JSON-safe dict."""
    return {
        "date": entry.date.isoformat(),
        "medication_name": entry.medication_name,
        "dose": entry.dose,
        "scheduled_time": entry.scheduled_time,
        "taken": entry.taken,
        "taken_at": entry.taken_at.isoformat() if entry.taken_at else None,
    }


def _serialize_exercise(entry: ExerciseLogEntry) -> dict[str, object]:
    """Convert an exercise log entry to a JSON-safe dict."""
    return {
        "date": entry.date.isoformat(),
        "exercise_type": entry.exercise_type,
        "duration_minutes": entry.duration_minutes,
        "completed": entry.completed,
        "notes": entry.notes,
        "logged_at": entry.logged_at.isoformat(),
    }


def _serialize_food(entry: FoodLogEntry) -> dict[str, object]:
    """Convert a food log entry to a JSON-safe dict."""
    return {
        "date": entry.date.isoformat(),
        "meal_type": entry.meal_type,
        "food_description": entry.food_description,
        "llm_notes": entry.llm_notes,
        "logged_at": entry.logged_at.isoformat(),
    }


def _serialize_meal_plan(entry: MealPlanEntry) -> dict[str, object]:
    """Convert a meal plan entry to a JSON-safe dict."""
    return {
        "week_start": entry.week_start.isoformat(),
        "day_of_week": entry.day_of_week,
        "meal_type": entry.meal_type,
        "suggestion": entry.suggestion,
        "nutrients_focus": entry.nutrients_focus,
    }


def execute_tool(tool_name: str, arguments: dict[str, object]) -> ToolExecutionResult:
    """Validate arguments and run one tool against the database."""
    if tool_name not in TOOL_NAMES:
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            result={},
            error=f"Unknown tool: {tool_name}",
        )

    try:
        payload = _dispatch_tool(tool_name, arguments)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            result=payload,
        )
    except ToolValidationError as error:
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            result={},
            error=str(error),
        )
    except Exception as error:
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            result={},
            error=f"Tool execution failed: {error}",
        )


def _dispatch_tool(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Run the validated tool handler."""
    if tool_name == "get_todays_logs":
        today = date.today()
        check_in = queries.get_daily_logs_for_date(today)
        food = queries.get_food_logs_for_date(today)
        exercise = queries.get_exercise_logs_for_date(today)
        return {
            "date": today.isoformat(),
            "check_in_logs": [_serialize_daily_log(entry) for entry in check_in],
            "food_logs": [_serialize_food(entry) for entry in food],
            "exercise_logs": [_serialize_exercise(entry) for entry in exercise],
            # Back-compat alias for older prompt wording.
            "logs": [_serialize_daily_log(entry) for entry in check_in],
        }

    if tool_name == "get_todays_schedule":
        entry_date = date.today()
        raw_date = arguments.get("date")
        if raw_date is not None:
            if not isinstance(raw_date, str):
                raise ToolValidationError("date must be a string.")
            entry_date = _parse_date_value(raw_date, "date")
        daily_plan = queries.get_daily_plan(entry_date)
        if daily_plan is None:
            return {"date": entry_date.isoformat(), "generated": False, "jobs": []}
        return {
            "date": entry_date.isoformat(),
            "generated": True,
            "summary": daily_plan.summary,
            "hydration_goal_liters": daily_plan.hydration_goal_liters,
            "jobs": [
                {
                    "job_id": job.job_id,
                    "type": job.type,
                    "time": job.time,
                    "message": job.message,
                    "volume_ml": job.volume_ml,
                    "exercise_type": job.exercise_type,
                    "duration_minutes": job.duration_minutes,
                }
                for job in daily_plan.jobs
            ],
        }

    if tool_name == "get_medications_today":
        entries = queries.get_medications_for_date(date.today())
        return {"medications": [_serialize_medication(entry) for entry in entries]}

    if tool_name == "get_meal_plan":
        entry_date = date.today()
        raw_date = arguments.get("date")
        if raw_date is not None:
            if not isinstance(raw_date, str):
                raise ToolValidationError("date must be a string.")
            entry_date = _parse_date_value(raw_date, "date")
        entries = queries.get_meal_plan_for_date(entry_date)
        return {
            "date": entry_date.isoformat(),
            "meals": [_serialize_meal_plan(entry) for entry in entries],
        }

    if tool_name == "get_weekly_summary":
        week = arguments.get("week", "current")
        if not isinstance(week, str) or week not in _WEEK_VALUES:
            raise ToolValidationError("week must be 'current' or 'previous'.")
        return queries.get_weekly_summary(week)

    if tool_name == "get_recent_logs":
        days = 7
        raw_days = arguments.get("days")
        if raw_days is not None:
            days = _require_int(arguments, "days", 1, 30)
        entries = queries.get_recent_logs(days)
        return {"logs": [_serialize_daily_log(entry) for entry in entries]}

    if tool_name == "log_medication_taken":
        medication_name = _require_string(arguments, "medication_name")
        scheduled_time = _parse_time_value(_require_string(arguments, "time"))
        updated = queries.mark_medication_taken(date.today(), medication_name, scheduled_time)
        return {"updated": updated, "medication_name": medication_name, "time": scheduled_time}

    if tool_name == "log_food":
        meal_type = _require_string(arguments, "meal_type").lower()
        if meal_type not in _MEAL_TYPES:
            raise ToolValidationError("meal_type must be breakfast, lunch, dinner, or snack.")
        food_description = _require_string(arguments, "food_description")
        log_id = queries.insert_food_log(
            FoodLogEntry(
                date=date.today(),
                meal_type=meal_type,
                food_description=food_description,
                logged_at=datetime.now(timezone.utc),
            )
        )
        return {"id": log_id, "meal_type": meal_type, "food_description": food_description}

    if tool_name == "log_water":
        cups = _require_int(arguments, "cups", 0, 30)
        queries.log_water(cups)
        return {"cups": cups, "date": date.today().isoformat()}

    if tool_name == "log_exercise":
        exercise_type = _require_string(arguments, "exercise_type")
        duration_minutes = _require_int(arguments, "duration_minutes", 1, 600)
        log_id = queries.insert_exercise_log(
            ExerciseLogEntry(
                date=date.today(),
                exercise_type=exercise_type,
                duration_minutes=duration_minutes,
                completed=True,
                logged_at=datetime.now(timezone.utc),
            )
        )
        return {
            "id": log_id,
            "exercise_type": exercise_type,
            "duration_minutes": duration_minutes,
        }

    if tool_name == "write_weekly_report":
        report_text = _require_string(arguments, "report_text")
        week_start = _parse_date_value(_require_string(arguments, "week_start"), "week_start")
        summary = queries.get_weekly_summary("current")
        report_id = queries.insert_weekly_report(
            WeeklyReport(
                week_start=week_start,
                report_text=report_text,
                water_goals_hit=0,
                medication_adherence=float(summary.get("medication_adherence_percent", 0.0)),
                exercises_completed=int(summary.get("exercises_completed", 0)),
                generated_at=datetime.now(timezone.utc),
            )
        )
        return {"id": report_id, "week_start": week_start.isoformat()}

    if tool_name == "save_meal_plan":
        week_start = _parse_date_value(_require_string(arguments, "week_start"), "week_start")
        raw_plan = arguments.get("plan")
        if not isinstance(raw_plan, list) or len(raw_plan) == 0:
            raise ToolValidationError("plan must be a non-empty array.")

        entries: list[MealPlanEntry] = []
        now = datetime.now(timezone.utc)
        for index, item in enumerate(raw_plan):
            if not isinstance(item, dict):
                raise ToolValidationError(f"plan[{index}] must be an object.")
            day_of_week = item.get("day_of_week")
            meal_type = item.get("meal_type")
            suggestion = item.get("suggestion")
            if not isinstance(day_of_week, str) or not day_of_week.strip():
                raise ToolValidationError(f"plan[{index}].day_of_week is required.")
            if not isinstance(meal_type, str) or meal_type.lower() not in _MEAL_TYPES:
                raise ToolValidationError(f"plan[{index}].meal_type is invalid.")
            if not isinstance(suggestion, str) or not suggestion.strip():
                raise ToolValidationError(f"plan[{index}].suggestion is required.")
            nutrients_focus = item.get("nutrients_focus", "")
            if not isinstance(nutrients_focus, str):
                nutrients_focus = str(nutrients_focus)
            entries.append(
                MealPlanEntry(
                    week_start=week_start,
                    day_of_week=day_of_week.strip(),
                    meal_type=meal_type.lower(),
                    suggestion=suggestion.strip(),
                    nutrients_focus=nutrients_focus,
                    generated_at=now,
                )
            )
        queries.insert_meal_plan_entries(entries)
        return {"saved_count": len(entries), "week_start": week_start.isoformat()}

    raise ToolValidationError(f"Unhandled tool: {tool_name}")


def tool_result_to_json(result: ToolExecutionResult) -> str:
    """Serialize a tool result for the LLM tool message channel."""
    payload: dict[str, object] = {
        "success": result.success,
        "tool": result.tool_name,
        "data": result.result,
    }
    if result.error:
        payload["error"] = result.error
    return json.dumps(payload)
