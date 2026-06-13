"""OpenAI-format tool schema definitions for Vitál."""

from typing import TypedDict


class ToolFunctionSchema(TypedDict):
    name: str
    description: str
    parameters: dict[str, object]


class ToolSchema(TypedDict):
    type: str
    function: ToolFunctionSchema


def _empty_object_schema() -> dict[str, object]:
    """Return an empty JSON-schema object for parameter-less tools."""
    return {"type": "object", "properties": {}, "additionalProperties": False}


TOOL_SCHEMAS: list[ToolSchema] = [
    {
        "type": "function",
        "function": {
            "name": "get_todays_logs",
            "description": (
                "Fetch today's wellness logs: check_in_logs (pain, water cups, etc.), "
                "food_logs (meals the user actually ate), and exercise_logs."
            ),
            "parameters": _empty_object_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_todays_schedule",
            "description": "Get today's LLM-generated schedule (hydration reminders, exercise block, summary).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_medications_today",
            "description": "Get today's medication schedule with taken or pending status.",
            "parameters": _empty_object_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_meal_plan",
            "description": "Get the meal plan for a specific date (defaults to today).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_summary",
            "description": "Get aggregated wellness stats for the current or previous week.",
            "parameters": {
                "type": "object",
                "properties": {
                    "week": {
                        "type": "string",
                        "enum": ["current", "previous"],
                        "description": "Which week to summarize.",
                    },
                },
                "required": ["week"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_logs",
            "description": "Get daily check-in logs for the last N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "description": "Number of days to include (default 7).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_medication_taken",
            "description": "Mark a scheduled medication dose as taken today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medication_name": {"type": "string"},
                    "time": {
                        "type": "string",
                        "description": "Scheduled time in HH:MM format.",
                    },
                },
                "required": ["medication_name", "time"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_food",
            "description": "Log a meal the user just ate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_type": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack"],
                    },
                    "food_description": {"type": "string"},
                },
                "required": ["meal_type", "food_description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_water",
            "description": "Update today's water cup count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cups": {"type": "integer", "minimum": 0, "maximum": 30},
                },
                "required": ["cups"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_exercise",
            "description": "Log a completed exercise session for today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_type": {"type": "string"},
                    "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 600},
                },
                "required": ["exercise_type", "duration_minutes"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_weekly_report",
            "description": "Save a generated weekly narrative report to the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report_text": {"type": "string"},
                    "week_start": {
                        "type": "string",
                        "description": "Monday of the week in YYYY-MM-DD format.",
                    },
                },
                "required": ["report_text", "week_start"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_meal_plan",
            "description": "Save an LLM-generated meal plan for a week.",
            "parameters": {
                "type": "object",
                "properties": {
                    "week_start": {
                        "type": "string",
                        "description": "Monday of the week in YYYY-MM-DD format.",
                    },
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "day_of_week": {"type": "string"},
                                "meal_type": {
                                    "type": "string",
                                    "enum": ["breakfast", "lunch", "dinner", "snack"],
                                },
                                "suggestion": {"type": "string"},
                                "nutrients_focus": {"type": "string"},
                            },
                            "required": ["day_of_week", "meal_type", "suggestion"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["week_start", "plan"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES: set[str] = {tool["function"]["name"] for tool in TOOL_SCHEMAS}
