from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal


DailyLogFieldType = Literal["scale_1_10", "number", "select", "boolean", "text"]
ScheduledJobType = Literal["medication", "hydration", "exercise", "break", "meal", "check_in"]
ScheduleType = Literal["daily_time", "interval_minutes", "weekly"]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]


@dataclass
class MedicationRecord:
    """A single medication or supplement entry on the user profile."""
    name: str
    dose: str
    time: str


@dataclass
class ProfileInput:
    """Payload for creating or updating the user profile."""
    name: str
    age: int
    city: str
    profession: str
    goal: str
    conditions: list[str]
    medications: list[MedicationRecord]
    triggers: list[str]
    wake_time: str
    sleep_time: str
    desk_worker: bool
    exercise_level: str
    dietary_notes: str
    local_foods: str


@dataclass
class ProfileRecord(ProfileInput):
    """A profile row read from the database."""
    id: int
    created_at: datetime
    updated_at: datetime


@dataclass
class DailyLogSchemaField:
    """Defines one dynamic daily check-in field for this user."""
    field_id: str
    label: str
    type: DailyLogFieldType
    display_order: int
    reason: str
    options: list[str] = field(default_factory=list)
    id: int | None = None


@dataclass
class DailyLogEntry:
    """One logged value for a daily check-in field."""
    date: date
    field_id: str
    value: str
    logged_at: datetime
    id: int | None = None


@dataclass
class MedicationLogEntry:
    """A scheduled medication dose for a given day."""
    date: date
    medication_name: str
    dose: str
    scheduled_time: str
    taken: bool
    taken_at: datetime | None
    id: int | None = None


@dataclass
class FoodLogEntry:
    """A logged meal entry."""
    date: date
    meal_type: MealType
    food_description: str
    logged_at: datetime
    llm_notes: str | None = None
    id: int | None = None


@dataclass
class ExerciseLogEntry:
    """A logged exercise session."""
    date: date
    exercise_type: str
    duration_minutes: int
    completed: bool
    logged_at: datetime
    notes: str | None = None
    id: int | None = None


@dataclass
class ScheduledJob:
    """A scheduler job loaded from the database."""
    job_id: str
    type: ScheduledJobType
    schedule_type: ScheduleType
    message: str
    tts: bool
    active: bool
    context: str
    time: str | None = None
    interval_minutes: int | None = None
    days: str | None = None
    id: int | None = None


@dataclass
class MealPlanEntry:
    """One meal suggestion in the weekly meal plan."""
    week_start: date
    day_of_week: str
    meal_type: MealType
    suggestion: str
    nutrients_focus: str
    generated_at: datetime
    id: int | None = None


@dataclass
class WeeklyReport:
    """An LLM-generated weekly wellness report."""
    week_start: date
    report_text: str
    water_goals_hit: int
    medication_adherence: float
    exercises_completed: int
    generated_at: datetime
    avg_pain: float | None = None
    id: int | None = None


@dataclass
class NotificationLogEntry:
    """A delivered notification record."""
    job_id: str
    message: str
    delivered_at: datetime
    tts_spoken: bool
    id: int | None = None


@dataclass
class PresenceLogEntry:
    """One presence-detection check result."""
    detected: bool
    checked_at: datetime
    continuous_minutes: int
    id: int | None = None


@dataclass
class PresenceConfig:
    """User-approved desk-break presence settings from onboarding."""
    enabled: bool
    max_continuous_minutes: int
    break_message: str
    break_duration_minutes: int
    check_interval_seconds: int = 300
