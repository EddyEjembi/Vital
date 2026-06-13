"""Types for LLM-generated daily schedules."""

from dataclasses import dataclass, field
from datetime import date, datetime

from vital_types.db import ScheduledJobType


@dataclass
class DailyScheduleJob:
    """One timed reminder in a daily plan."""
    job_id: str
    type: ScheduledJobType
    time: str
    message: str
    tts: bool
    context: str
    volume_ml: int | None = None
    exercise_type: str | None = None
    duration_minutes: int | None = None
    id: int | None = None


@dataclass
class DailyPlan:
    """A complete schedule for one calendar day."""
    plan_date: date
    summary: str
    hydration_goal_liters: float
    generated_at: datetime
    jobs: list[DailyScheduleJob] = field(default_factory=list)
    id: int | None = None
